"""Desktop local runner controller and FastAPI lifespan wiring (split module).

The controller owns the desktop runner lifecycle: authenticate, compose the
model/tools, start N workers plus the derivation and unattended verification
loops, and stop everything again.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.domain import (
    ArtifactRef,
    EvidenceVerdict,
)
from app.services.artifact_integrity_service import (
    ArtifactByteVerificationError,
    ArtifactByteVerifier,
    build_artifact_byte_verifier,
)
from app.services.artifact_store_service import ArtifactPublisher
from app.services.desktop_guidance import (
    GuidanceSourcePort,
    InProcessGuidanceSource,
    MissionControlGuidanceSource,
)
from app.services.desktop_mcp_bridge import (
    DesktopMcpBridge,
    load_mcp_server_configs,
)
from app.services.desktop_mission_memory import (
    MISSION_MEMORY_BODY_SUMMARY_CHARS,
    DesktopMissionMemorySink,
    MissionMemorySinkPort,
)
from app.services.desktop_runner_tools import (
    DelegateSubtaskConfig,
    build_desktop_runner_tools,
)
from app.services.tools.policy import resolve_tool_execution_policy
from app.services.harness_service import FunctionTool
from app.services.mission_service import DESKTOP_TASK_WORK_UNIT_KIND
from app.services.runner.auth import DesktopAuthenticator
from app.services.runner.loops import (
    MissionControlDesktopMissionSource,
    DesktopMissionSourcePort,
    _verify_command_failure_summary,
    derive_desktop_task_work_units,
    extract_run_commands,
    extract_verify_commands,
    run_verify_command,
)
from app.services.runner.model import (
    DesktopTaskHarnessFactory,
    _NoCapabilityBindings,
)
from app.services.runner.settings import (
    _DESKTOP_EVALUATOR,
    _MAX_DESKTOP_WORKERS,
    DESKTOP_ADAPTER_TYPE,
    DESKTOP_AGENT_ID,
    DESKTOP_VERIFIER_ID,
    DESKTOP_VERIFIER_VERSION,
    INPROCESS_GUIDANCE_ENV,
    DesktopLocalRunnerSettings,
)
from app.services.runner_checkpoint import MissionControlHarnessCheckpointFactory
from app.services.runner_composition import (
    HarnessModelFactoryPort,
    build_kind_aware_workspace_runner,
)
from app.services.runner_service import (
    DesktopTaskClaimedWorkResolver,
    MissionControlRunnerClient,
    MissionControlRunnerPort,
    WorkUnitRunner,
)
from app.services.runner_worker import RunnerWorker
from app.services.verifier_service import (
    MissionControlVerifierClient,
    VerificationSubmission,
)

logger = logging.getLogger("agenthub.desktop_local_runner")


class DesktopVerifierControlPort(Protocol):
    """Verifier commands against Mission Control (HTTP adapter in production)."""

    async def discover_verification_work(
        self,
        workspace_id: str,
    ) -> dict[str, Any]: ...

    async def submit_verification(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        submission: VerificationSubmission,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class _VerificationArtifactBytes:
    """Minimal Artifact descriptor for byte verification."""

    id: str
    digest: str
    content_address: str
    size_bytes: int


# ── Controller ───────────────────────────────────────────────────────────


async def resolve_default_model_factory(
    settings: DesktopLocalRunnerSettings,
) -> Any:
    """Compose the admin-configured model factory for one runner.

    Resolution goes through the public ``app.services.desktop_local_runner``
    facade so the historical patch points (tests patch the facade attributes
    ``load_default_model_config`` / ``DesktopModelFactory``) keep working
    after the module split. The import is call-time: the facade itself
    star-imports this package, so a module-level import would cycle.
    """
    from app.services import desktop_local_runner as facade

    config = await facade.load_default_model_config(settings.model_name)
    return facade.DesktopModelFactory(
        config,
        context_char_budget=settings.context_char_budget,
    )


class DesktopLocalRunnerController:
    """Own the desktop runner lifecycle: start, derive, poll, stop."""

    def __init__(
        self,
        settings: DesktopLocalRunnerSettings,
        *,
        control: MissionControlRunnerPort | None = None,
        publisher: ArtifactPublisher | None = None,
        model_factory: HarnessModelFactoryPort | None = None,
        mission_source: DesktopMissionSourcePort | None = None,
        authenticator: DesktopAuthenticator | None = None,
        workspace_root: Path | None = None,
        tools: Sequence[FunctionTool] | None = None,
        max_result_chars: int | None = None,
        verifier_control: DesktopVerifierControlPort | None = None,
        byte_verifier: ArtifactByteVerifier | None = None,
        guidance_source: GuidanceSourcePort | None = None,
        memory_sink: MissionMemorySinkPort | None = None,
    ) -> None:
        self._settings = settings
        self._injected_control = control
        self._publisher = publisher
        self._injected_model_factory = model_factory
        self._mission_source = mission_source
        self._authenticator = authenticator or DesktopAuthenticator()
        self._workspace_root = (
            workspace_root or settings.default_workspace_root()
        ).resolve()
        self._max_result_chars = max_result_chars
        self._tools = list(tools) if tools is not None else None
        self._verifier_control = verifier_control
        self._byte_verifier = byte_verifier
        self._guidance_source = guidance_source
        # P3-1c: one controller-level consumption ledger shared by every
        # worker's guidance source, so a guidance event is injected exactly
        # once no matter which worker's model call observes it first.
        self._guidance_ledger: set[str] = set()
        self._memory_sink = memory_sink
        self._verifier: DesktopVerifierControlPort | None = None
        self._mcp_bridge: DesktopMcpBridge | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._workers: list[RunnerWorker] = []
        self._worker_tasks: list[asyncio.Task[Any]] = []
        self._derivation_task: asyncio.Task[Any] | None = None
        self._verification_task: asyncio.Task[Any] | None = None
        self._stop_event = asyncio.Event()
        self._runner: WorkUnitRunner | None = None

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    async def start(self) -> None:
        if self._worker_tasks:
            raise RuntimeError("desktop local runner is already running")
        self._stop_event.clear()
        settings = self._settings

        if self._injected_control is not None:
            control = self._injected_control
            runner_id = settings.user_id or DESKTOP_RUNNER_LABEL
            verifier = self._verifier_control
        else:
            identity = await self._authenticator.resolve(settings)
            self._http_client = httpx.AsyncClient()
            control = MissionControlRunnerClient(
                settings.base_url,
                access_token=identity.access_token,
                http_client=self._http_client,
            )
            runner_id = identity.user_id
            verifier = self._verifier_control or MissionControlVerifierClient(
                settings.base_url,
                access_token=identity.access_token,
                http_client=self._http_client,
            )
            if self._guidance_source is None:
                self._guidance_source = self._build_default_guidance_source(
                    access_token=identity.access_token,
                )
        self._verifier = verifier

        model_factory = self._injected_model_factory
        if model_factory is None:
            model_factory = await resolve_default_model_factory(settings)

        # Plain conversational turns explicitly disable tool schemas so the
        # model can answer immediately instead of entering the desktop tool
        # loop. Read-only coding turns keep the normal suggest whitelist.
        disable_tools = os.environ.get("AGENTHUB_DESKTOP_DISABLE_TOOLS", "0") == "1"
        policy = resolve_tool_execution_policy(
            self._workspace_root,
            environment_value=os.environ.get("AGENTHUB_TOOL_PERMISSION_MODE"),
        )
        if disable_tools:
            tools = []
        elif self._tools is not None:
            tools = list(self._tools)
        elif self._max_result_chars is not None:
            tools = build_desktop_runner_tools(
                self._workspace_root,
                max_result_chars=self._max_result_chars,
                model_factory=model_factory,
                subtask_config=DelegateSubtaskConfig(
                    max_tool_calls=settings.max_tool_calls,
                    max_total_tokens=settings.max_total_tokens,
                    timeout_seconds=settings.timeout_seconds,
                ),
                sandbox_enabled=settings.sandbox_enabled,
                policy=policy,
            )
        else:
            tools = build_desktop_runner_tools(
                self._workspace_root,
                model_factory=model_factory,
                subtask_config=DelegateSubtaskConfig(
                    max_tool_calls=settings.max_tool_calls,
                    max_total_tokens=settings.max_total_tokens,
                    timeout_seconds=settings.timeout_seconds,
                ),
                sandbox_enabled=settings.sandbox_enabled,
                policy=policy,
            )

        mcp_bridge = await self._build_mcp_bridge()
        if mcp_bridge is not None:
            mcp_tools = await mcp_bridge.build_tools()
            tools.extend(mcp_tools)
            logger.info("desktop runner MCP bridge added %d tool(s)", len(mcp_tools))
        self._mcp_bridge = mcp_bridge

        worker_count = max(1, min(settings.workers, _MAX_DESKTOP_WORKERS))
        for index in range(worker_count):
            worker_runner_id = (
                runner_id if worker_count == 1 else f"{runner_id}-w{index}"
            )
            runner = self._build_runner(
                control,
                runner_id=worker_runner_id,
                model_factory=model_factory,
                tools=tools,
            )
            if index == 0:
                self._runner = runner
            worker = RunnerWorker(
                runner,
                workspace_id=settings.workspace_id,
                lease_seconds=settings.lease_seconds,
                idle_delay_seconds=settings.idle_delay_seconds,
                max_delay_seconds=settings.max_delay_seconds,
            )
            self._workers.append(worker)
        self._derivation_task = asyncio.create_task(self._derivation_loop())
        self._worker_tasks = [
            asyncio.create_task(worker.run()) for worker in self._workers
        ]
        if settings.verify_enabled and self._verifier is not None:
            self._verification_task = asyncio.create_task(self._verification_loop())
        logger.info(
            "desktop local runner started: workspace=%s root=%s runner=%s "
            "workers=%d verify=%s mcp=%s",
            settings.workspace_id,
            self._workspace_root,
            runner_id,
            worker_count,
            "on" if self._verification_task is not None else "off",
            "on" if mcp_bridge is not None else "off",
        )

    def _build_default_guidance_source(
        self,
        *,
        access_token: str,
    ) -> GuidanceSourcePort:
        """Default guidance source: in-process first, HTTP feed fallback.

        The desktop runner shares the Mission Control process and database,
        so reading the guidance ledger through the local repository skips an
        HTTP round-trip; ``AGENTHUB_DESKTOP_LOCAL_RUNNER_INPROCESS=0`` opts
        back into the HTTP feed. Both share the controller-level consumption
        ledger (P3-1c).
        """
        if os.environ.get(INPROCESS_GUIDANCE_ENV, "1").strip() != "0":
            return InProcessGuidanceSource(
                consumed_event_ids=self._guidance_ledger,
            )
        assert self._http_client is not None
        return MissionControlGuidanceSource(
            self._settings.base_url,
            access_token=access_token,
            http_client=self._http_client,
            consumed_event_ids=self._guidance_ledger,
        )

    async def _build_mcp_bridge(self) -> DesktopMcpBridge | None:
        """Build the optional MCP bridge from env config; degrade on error."""
        if self._settings.mcp_config is None:
            return None
        try:
            configs = load_mcp_server_configs(self._settings.mcp_config)
        except Exception as exc:  # noqa: BLE001 - MCP is optional
            logger.warning(
                "desktop runner MCP config %s is invalid, MCP tools disabled: %s",
                self._settings.mcp_config,
                exc,
            )
            return None
        return DesktopMcpBridge(configs)

    async def stop(self) -> None:
        self._stop_event.set()
        for worker in self._workers:
            worker.request_stop()
        for task in (
            *self._worker_tasks,
            self._derivation_task,
            self._verification_task,
        ):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker_tasks = []
        self._workers = []
        self._derivation_task = None
        self._verification_task = None
        self._runner = None
        if self._mcp_bridge is not None:
            await self._mcp_bridge.aclose()
            self._mcp_bridge = None
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        logger.info("desktop local runner stopped")

    def _build_runner(
        self,
        control: MissionControlRunnerPort,
        *,
        runner_id: str,
        model_factory: HarnessModelFactoryPort,
        tools: Sequence[FunctionTool],
    ) -> WorkUnitRunner:
        settings = self._settings
        harness_factory = DesktopTaskHarnessFactory(
            model_factory,
            tools=tools,
            checkpoint_factory=MissionControlHarnessCheckpointFactory(
                control,
                runner_id=runner_id,
            ),
            guidance_source=self._guidance_source,
            max_iterations=settings.max_iterations,
            max_tool_calls=settings.max_tool_calls,
            max_total_tokens=settings.max_total_tokens,
        )
        publisher = self._publisher
        if publisher is None:
            from app.services.artifact_store_service import build_artifact_publisher

            publisher = build_artifact_publisher()
        return build_kind_aware_workspace_runner(
            control,
            publisher=publisher,
            model_factory=model_factory,
            binding_factory=_NoCapabilityBindings(),
            runner_id=runner_id,
            assigned_agent_id=DESKTOP_AGENT_ID,
            assigned_adapter=DESKTOP_ADAPTER_TYPE,
            max_timeout_seconds=settings.timeout_seconds,
            max_iterations=settings.max_iterations,
            max_tool_calls=settings.max_tool_calls,
            max_total_tokens=settings.max_total_tokens,
            extra_resolvers={
                DESKTOP_TASK_WORK_UNIT_KIND: DesktopTaskClaimedWorkResolver(
                    control,
                    runner_id=runner_id,
                    harness_factory=harness_factory,
                    max_timeout_seconds=settings.timeout_seconds,
                ),
            },
        )

    async def _derivation_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._derive_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "desktop runner derivation failed: %s",
                    exc,
                    exc_info=True,
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._settings.derivation_interval_seconds,
                )
            except TimeoutError:
                continue

    async def _derive_once(self) -> None:
        mission_source = self._mission_source
        if mission_source is None:
            mission_source = MissionControlDesktopMissionSource()
        await derive_desktop_task_work_units(
            mission_source,
            workspace_id=self._settings.workspace_id,
        )

    async def _verification_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._verify_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "desktop runner verification failed: %s",
                    exc,
                    exc_info=True,
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._settings.verify_interval_seconds,
                )
            except TimeoutError:
                continue

    async def _verify_once(self) -> None:
        """Discover one VERIFYING item and submit Evidence for artifact facts.

        Everything runs through the Mission Control verifier API; the loop
        only judges registered Artifact bytes (existence, non-empty size,
        SHA-256 digest) — never its own execution result.
        """
        verifier = self._verifier
        if verifier is None:
            return
        discovery = await verifier.discover_verification_work(
            self._settings.workspace_id
        )
        if not isinstance(discovery, Mapping):
            return
        context = discovery.get("verificationContext")
        if discovery.get("discoveryStatus") != "ready" or not isinstance(
            context, Mapping
        ):
            return
        policy = context.get("evaluationPolicy")
        if not isinstance(policy, Mapping) or policy.get("status") != "ready":
            # Manual / inconclusive policies stay with the human decision flow.
            return
        if policy.get("evaluator") != _DESKTOP_EVALUATOR:
            return
        work_unit = context.get("workUnit")
        mission = context.get("mission")
        if not isinstance(work_unit, Mapping) or not isinstance(mission, Mapping):
            return
        artifacts = [
            artifact
            for artifact in context.get("artifacts") or []
            if isinstance(artifact, Mapping)
        ]
        if not artifacts:
            return

        failure_reason = await self._check_artifact_facts(artifacts)
        objective = str(mission.get("objective") or "")
        verify_commands = extract_verify_commands(objective)
        run_commands = extract_run_commands(objective)
        if failure_reason is None and verify_commands:
            failure_reason = await self._run_verify_commands(verify_commands)
        if failure_reason is None and run_commands:
            failure_reason = await self._run_verify_commands(
                run_commands, label="Run command"
            )
        if failure_reason is None:
            verdict = EvidenceVerdict.PASS
            summary = (
                f"{_DESKTOP_EVALUATOR} verified {len(artifacts)} Artifact(s): "
                "files exist, are non-empty and byte digests match."
            )
            if verify_commands:
                summary += " Verify command(s) passed: " + "; ".join(verify_commands)
            if run_commands:
                summary += " Run command(s) passed: " + "; ".join(run_commands)
        else:
            verdict = EvidenceVerdict.FAIL
            summary = f"desktop verification failed: {failure_reason}"
        submission = VerificationSubmission(
            criterion_id=str(policy.get("criterionId")),
            verifier_id=DESKTOP_VERIFIER_ID,
            verifier_version=DESKTOP_VERIFIER_VERSION,
            configuration_digest=str(policy.get("configurationDigest")),
            verdict=verdict,
            artifact_refs=tuple(
                ArtifactRef(
                    id=str(artifact.get("id")),
                    digest=str(artifact.get("digest")),
                )
                for artifact in artifacts
            ),
            summary=summary,
        )
        await verifier.submit_verification(
            str(mission.get("id")),
            str(work_unit.get("id")),
            submission=submission,
        )
        logger.info(
            "desktop runner verification submitted: work_unit=%s verdict=%s reason=%s",
            work_unit.get("id"),
            verdict.value,
            failure_reason or "artifact bytes verified",
        )
        if verdict is EvidenceVerdict.PASS:
            await self._save_mission_memory(
                str(mission.get("id")),
                objective=objective,
                artifacts=artifacts,
            )

    async def _save_mission_memory(
        self,
        mission_id: str,
        *,
        objective: str,
        artifacts: Sequence[Mapping[str, Any]],
    ) -> None:
        """Persist one cross-task memory entry after a PASS verdict (P1-2).

        The final model summary is the first registered Artifact's bytes;
        failures degrade to a warning — memory deposition never flips the
        already-submitted verdict.
        """
        sink = self._memory_sink
        if sink is None:
            sink = DesktopMissionMemorySink()
        summary = ""
        if artifacts:
            try:
                descriptor = _VerificationArtifactBytes(
                    id=str(artifacts[0].get("id")),
                    digest=str(artifacts[0].get("digest")),
                    content_address=str(artifacts[0].get("contentAddress")),
                    size_bytes=int(artifacts[0].get("sizeBytes") or 0),
                )
                byte_verifier = self._byte_verifier
                if byte_verifier is None:
                    byte_verifier = build_artifact_byte_verifier()
                content = await byte_verifier.read_verified(
                    descriptor,
                    max_bytes=4 * MISSION_MEMORY_BODY_SUMMARY_CHARS,
                )
                summary = content.decode("utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001 - best-effort deposition
                logger.warning(
                    "desktop runner could not read summary artifact for "
                    "mission %s: %s",
                    mission_id,
                    exc,
                )
        try:
            saved = await sink.save_mission_summary(
                mission_id,
                objective=objective,
                summary=summary,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort deposition
            logger.warning(
                "desktop runner memory deposition failed for mission %s: %s",
                mission_id,
                exc,
            )
            return
        if saved:
            logger.info(
                "desktop runner deposited memory for mission %s", mission_id
            )

    async def _check_artifact_facts(
        self,
        artifacts: Sequence[Mapping[str, Any]],
    ) -> str | None:
        """Return ``None`` when every Artifact's bytes check out, else the reason."""
        descriptors: list[_VerificationArtifactBytes] = []
        for artifact in artifacts:
            size_bytes = artifact.get("sizeBytes")
            artifact_id = str(artifact.get("id"))
            if not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
                return f"artifact {artifact_id} has an invalid registered size"
            if size_bytes <= 0:
                return f"artifact {artifact_id} is empty"
            descriptors.append(
                _VerificationArtifactBytes(
                    id=artifact_id,
                    digest=str(artifact.get("digest")),
                    content_address=str(artifact.get("contentAddress")),
                    size_bytes=size_bytes,
                )
            )
        byte_verifier = self._byte_verifier
        if byte_verifier is None:
            byte_verifier = build_artifact_byte_verifier()
        try:
            await byte_verifier.verify_all(descriptors)
        except ArtifactByteVerificationError as exc:
            return str(exc)
        return None

    async def _run_verify_commands(
        self,
        commands: Sequence[str],
        *,
        label: str = "verify command",
    ) -> str | None:
        """Run declared objective commands; ``None`` means all passed.

        Each command runs in the workspace root with the configured timeout;
        the first failing command produces the Evidence summary (output tail
        included) that drives the FAIL verdict.
        """
        for command in commands:
            outcome = await run_verify_command(
                command,
                cwd=self._workspace_root,
                timeout_seconds=self._settings.verify_command_timeout_seconds,
                sandbox_enabled=self._settings.sandbox_enabled,
            )
            if outcome.timed_out or outcome.exit_code != 0:
                return _verify_command_failure_summary(outcome, label=label)
        return None


# ── FastAPI lifespan wiring ──────────────────────────────────────────────


def desktop_local_runner_settings() -> DesktopLocalRunnerSettings:
    return DesktopLocalRunnerSettings.from_env()


async def startup_desktop_local_runner(
    app: Any,
    *,
    settings: DesktopLocalRunnerSettings | None = None,
    controller_factory: Any = None,
) -> None:
    """Start the desktop local runner when the env gate is on.

    ``controller_factory`` exists for tests; production resolves the
    controller from the settings.
    """
    # Start the optional cross-process Mission event listener alongside the
    # runner. SQLite/Neon profiles simply omit DATABASE_URL and retain the
    # in-process bus fallback.
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url.startswith(("postgres://", "postgresql://")):
        try:
            from app.services.mission_event_bus import PostgresMissionEventNotifier, mission_event_bus
            notifier = PostgresMissionEventNotifier(database_url, mission_event_bus)
            await notifier.start()
            app.state.mission_event_notifier = notifier
        except Exception:
            logger.debug("postgres mission listener unavailable; using local bus", exc_info=True)
    resolved = settings or desktop_local_runner_settings()
    if not resolved.enabled:
        return
    if controller_factory is not None:
        controller = controller_factory(resolved)
    else:
        controller = DesktopLocalRunnerController(resolved)
    await controller.start()
    app.state.desktop_local_runner = controller


async def shutdown_desktop_local_runner(app: Any) -> None:
    notifier = getattr(app.state, "mission_event_notifier", None)
    if notifier is not None:
        await notifier.stop()
        app.state.mission_event_notifier = None
    controller = getattr(app.state, "desktop_local_runner", None)
    if controller is None:
        return
    await controller.stop()
    app.state.desktop_local_runner = None
