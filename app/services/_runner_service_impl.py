from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.services.artifact_store_service import (
    ArtifactPublisher,
)
from app.services.harness_service import (
    HarnessExecutionContext,
    HarnessPort,
    HarnessRequest,
    HarnessResumeInput,
    SandboxHarness,
)
from app.services.runner_client import MissionControlRunnerClient
from app.services.runner_context_validation import (  # noqa: F401 - compatibility exports
    _A2A_INBOUND_CONTEXT_PROFILE,
    _DESKTOP_TASK_CONTEXT_PROFILE,
    _MISSION_FORK_CONTEXT_PROFILE,
    _ModelContextProfile,
    _optional_string,
    _required_mapping,
    _required_non_negative_int,
    _required_sequence,
    _required_string,
    _sequence_string,
    _string_list,
)
from app.services.runner_model_context import (
    compile_mission_fork_context,
)
from app.services.runner_model_resolver import (  # noqa: F401 - compatibility exports
    A2AInboundClaimedWorkResolver,
    DesktopTaskClaimedWorkResolver,
    KindAwareClaimedWorkResolver,
    MissionForkClaimedWorkResolver,
    _ClaimedModelWorkResolver,
)
from app.services.runner_protocols import (  # noqa: F401 - compatibility exports
    ClaimedHarnessFactoryPort,
    ClaimedWorkExecution,
    ClaimedWorkResolutionError,
    ClaimedWorkResolver,
    MissionControlRunnerPort,
    RunnerControlError,
    RunnerError,
    RunnerExecutionError,
    RunnerExecutionInput,
    RunnerHeartbeatError,
    RunnerRunResult,
    RunnerWorkspacePollResult,
    SandboxPort,
    _LeaseContext,
)
from app.services.runner_sync import run_mission_sync
from app.services.recovery_lock import RecoveryExecutionBusy
from app.services.tools.sandbox_executor import SandboxExecutor, SandboxResult
from app.services.workspace_admission_service import WorkspaceClaimStatus
from app.services.mission._workspace_claim import validate_resume_mission_id
from app.services.runner_recovery_scope import runner_recovery_scope
from app.services.workspace_fingerprint import (
    context_manifest_digest,
)

logger = logging.getLogger("agenthub.runner")


def _validate_supported_capabilities(value: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError("supported_capabilities must be a tuple")
    if len(value) > 256:
        raise ValueError("supported_capabilities exceeds limit")
    if any(not isinstance(capability, str) or not capability.strip()
           or capability != capability.strip() or len(capability) > 255 for capability in value):
        raise ValueError("supported_capabilities is invalid")
    if len(value) != len(set(value)):
        raise ValueError("supported_capabilities must be unique")
    return value


class WorkUnitRunner:
    """Runs one bounded command and reports only through Mission Control."""

    def __init__(
        self,
        control: MissionControlRunnerPort,
        *,
        publisher: ArtifactPublisher,
        sandbox: SandboxPort | None = None,
        harness: HarnessPort | None = None,
        runner_id: str,
        assigned_agent_id: str | None = None,
        assigned_adapter: str | None = None,
        claimed_work_resolver: ClaimedWorkResolver | None = None,
        heartbeat_interval_seconds: float | None = None,
        workspace_claims_enabled: bool = True,
        supported_work_unit_kinds: tuple[str, ...] | None = None,
        supported_capabilities: tuple[str, ...] = (),
        resume_mission_id: str | None = None,
        on_text_delta: Any | None = None,
    ) -> None:
        self._control = control
        self._publisher = publisher
        self._sandbox = sandbox or SandboxExecutor()
        self._harness = harness or SandboxHarness(self._sandbox)
        self._runner_id = runner_id
        if (assigned_agent_id is None) != (assigned_adapter is None):
            raise ValueError(
                "assigned_agent_id and assigned_adapter must be configured together"
            )
        self._assigned_agent_id = assigned_agent_id
        self._assigned_adapter = assigned_adapter
        self._claimed_work_resolver = claimed_work_resolver
        if heartbeat_interval_seconds is not None and heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        if type(workspace_claims_enabled) is not bool:
            raise TypeError("workspace_claims_enabled must be a boolean")
        self._workspace_claims_enabled = workspace_claims_enabled
        if supported_work_unit_kinds is not None:
            if not supported_work_unit_kinds:
                raise ValueError("supported_work_unit_kinds must be non-empty")
            if len(supported_work_unit_kinds) > 32:
                raise ValueError("supported_work_unit_kinds exceeds limit")
            if len(supported_work_unit_kinds) != len(set(supported_work_unit_kinds)):
                raise ValueError("supported_work_unit_kinds must be unique")
            if any(
                not isinstance(kind, str)
                or not kind.strip()
                or kind != kind.strip()
                or len(kind) > 255
                for kind in supported_work_unit_kinds
            ):
                raise ValueError("supported_work_unit_kinds is invalid")
        self._supported_work_unit_kinds = supported_work_unit_kinds
        self._supported_capabilities = _validate_supported_capabilities(supported_capabilities)
        validate_resume_mission_id(resume_mission_id)
        self._resume_mission_id = resume_mission_id
        self._on_text_delta = on_text_delta

    async def run(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        code: str,
        language: str = "python",
        timeout: float = 30.0,
        cwd: Path | None = None,
        lease_seconds: int = 300,
        artifact_kind: str = "test-result",
        media_type: str = "text/plain",
    ) -> RunnerRunResult:
        leased = await self._control.lease_work_unit(
            mission_id,
            work_unit_id,
            runner_id=self._runner_id,
            lease_seconds=lease_seconds,
        )
        return await self._run_leased(
            mission_id,
            work_unit_id,
            leased,
            code=code,
            language=language,
            timeout=timeout,
            cwd=cwd,
            lease_seconds=lease_seconds,
            artifact_kind=artifact_kind,
            media_type=media_type,
            harness=self._harness,
        )

    async def resume(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        leased: Mapping[str, Any],
        code: str,
        language: str = "text",
        timeout: float = 30.0,
        cwd: Path | None = None,
        resume: HarnessResumeInput,
        lease_seconds: int = 300,
        artifact_kind: str = "test-result",
        media_type: str = "text/plain",
        harness: HarnessPort | None = None,
    ) -> RunnerRunResult:
        """Continue a checkpointed turn under an existing lease."""
        if not isinstance(resume, HarnessResumeInput):
            raise TypeError("resume must be a HarnessResumeInput")
        return await self._run_leased(
            mission_id,
            work_unit_id,
            leased,
            code=code,
            language=language,
            timeout=timeout,
            cwd=cwd,
            lease_seconds=lease_seconds,
            artifact_kind=artifact_kind,
            media_type=media_type,
            harness=harness or self._harness,
            resume=resume,
        )

    async def claim_and_run(
        self,
        mission_id: str,
        *,
        lease_seconds: int = 300,
        artifact_kind: str = "test-result",
        media_type: str = "text/plain",
    ) -> RunnerRunResult | None:
        """Claim and execute one WorkUnit for this Runner binding."""
        agent_id, adapter_type = self._claim_binding()
        claimed_payload = await self._control.claim_work_unit(
            mission_id,
            runner_id=self._runner_id,
            agent_id=agent_id,
            adapter_type=adapter_type,
            lease_seconds=lease_seconds,
        )
        return await self._run_claimed_payload(
            claimed_payload,
            expected_mission_id=mission_id,
            lease_seconds=lease_seconds,
            artifact_kind=artifact_kind,
            media_type=media_type,
        )

    async def claim_ready_and_run(
        self,
        workspace_id: str,
        *,
        lease_seconds: int = 300,
        artifact_kind: str = "test-result",
        media_type: str = "text/plain",
    ) -> RunnerWorkspacePollResult:
        """Discover, claim, and execute one bound WorkUnit in a workspace."""

        if not self._workspace_claims_enabled:
            raise RunnerControlError(
                "workspace claims are disabled for this Runner composition"
            )
        if self._supported_work_unit_kinds is None:
            raise RunnerControlError(
                "workspace claims require explicit supported WorkUnit kinds"
            )
        if not workspace_id.strip():
            raise ValueError("workspace_id must be non-empty")
        agent_id, adapter_type = self._claim_binding()
        claimed_payload = await self._control.claim_ready_work_unit(
            workspace_id,
            runner_id=self._runner_id,
            agent_id=agent_id,
            adapter_type=adapter_type,
            supported_work_unit_kinds=self._supported_work_unit_kinds,
            lease_seconds=lease_seconds,
            **({"supported_capabilities": self._supported_capabilities} if self._supported_capabilities else {}),
            **({"resume_mission_id": self._resume_mission_id} if self._resume_mission_id is not None else {}),
        )
        claim_status = parse_workspace_claim_status(claimed_payload)
        try:
            run_result = await self._run_claimed_payload(
                claimed_payload,
                expected_mission_id=self._resume_mission_id,
                lease_seconds=lease_seconds,
                artifact_kind=artifact_kind,
                media_type=media_type,
            )
        except RecoveryExecutionBusy:
            return RunnerWorkspacePollResult(WorkspaceClaimStatus.CAPACITY_SATURATED, None)
        return RunnerWorkspacePollResult(
            claim_status=claim_status,
            run_result=run_result,
        )

    def _claim_binding(self) -> tuple[str, str]:
        if self._assigned_agent_id is None or self._assigned_adapter is None:
            raise RunnerControlError(
                "claim requires an assigned agent and adapter binding"
            )
        return self._assigned_agent_id, self._assigned_adapter

    async def _run_claimed_payload(
        self,
        claimed_payload: Mapping[str, Any],
        *,
        expected_mission_id: str | None,
        lease_seconds: int,
        artifact_kind: str,
        media_type: str,
    ) -> RunnerRunResult | None:
        work_unit_payload = claimed_payload.get("workUnit")
        if work_unit_payload is None:
            return None
        if not isinstance(work_unit_payload, Mapping):
            raise RunnerControlError("Mission Control claim response has no WorkUnit")
        mission_id = work_unit_payload.get("missionId")
        if not isinstance(mission_id, str) or not mission_id.strip():
            raise RunnerControlError(
                "Mission Control claim response has no Mission id"
            )
        if expected_mission_id is not None and mission_id != expected_mission_id:
            raise RunnerControlError(
                "Mission Control returned a WorkUnit for another mission"
            )
        agent_id, adapter_type = self._claim_binding()
        assert_claimed_work_unit(
            work_unit_payload,
            mission_id=mission_id,
            runner_id=self._runner_id,
            agent_id=agent_id,
            adapter_type=adapter_type,
        )
        work_unit_id = work_unit_payload.get("id")
        if not isinstance(work_unit_id, str) or not work_unit_id:
            raise RunnerControlError("Mission Control claim response has no WorkUnit id")
        lease = _lease_context(work_unit_payload)
        lease_payload = work_unit_payload.get("lease")
        if not isinstance(lease_payload, Mapping) or str(lease_payload.get("runnerId") or "") != self._runner_id:
            raise RunnerControlError("claimed WorkUnit lease belongs to another runner")
        resolver = self._claimed_work_resolver
        if resolver is None:
            await self._fail(
                mission_id,
                work_unit_id,
                lease,
                "claimed WorkUnit has no execution resolver",
            )
            raise RunnerExecutionError(
                "claimed WorkUnit cannot execute without a trusted resolver"
            )
        try:
            execution = await resolver.resolve(work_unit_payload)
            _require_running_resume(work_unit_payload, execution)
        except RecoveryExecutionBusy:
            raise
        except Exception as exc:
            await self._fail(
                mission_id,
                work_unit_id,
                lease,
                f"claimed WorkUnit input resolution failed: {exc}",
            )
            raise RunnerExecutionError(
                "claimed WorkUnit input resolution failed"
            ) from exc
        if (
            not isinstance(execution, ClaimedWorkExecution)
            or not isinstance(execution.execution_input, RunnerExecutionInput)
            or not callable(getattr(execution.harness, "execute", None))
        ):
            await self._fail(
                mission_id,
                work_unit_id,
                lease,
                "claimed WorkUnit resolver returned an invalid execution plan",
            )
            raise RunnerExecutionError("claimed WorkUnit resolver returned invalid plan")
        execution_input = execution.execution_input
        if execution_input.resume is not None:
            return await self.resume(
                mission_id,
                work_unit_id,
                leased=work_unit_payload,
                code=execution_input.code,
                language=execution_input.language,
                timeout=execution_input.timeout,
                cwd=execution_input.cwd,
                resume=execution_input.resume,
                lease_seconds=lease_seconds,
                artifact_kind=artifact_kind,
                media_type=media_type,
                harness=execution.harness,
            )
        return await self._run_leased(
            mission_id,
            work_unit_id,
            work_unit_payload,
            code=execution_input.code,
            language=execution_input.language,
            timeout=execution_input.timeout,
            cwd=execution_input.cwd,
            resume=execution_input.resume,
            lease_seconds=lease_seconds,
            artifact_kind=artifact_kind,
            media_type=media_type,
            harness=execution.harness,
        )

    async def _run_leased(
        self,
        mission_id: str,
        work_unit_id: str,
        leased: Mapping[str, Any],
        *,
        code: str,
        language: str,
        timeout: float,
        cwd: Path | None,
        lease_seconds: int,
        artifact_kind: str,
        media_type: str,
        harness: HarnessPort,
        resume: HarnessResumeInput | None = None,
    ) -> RunnerRunResult:
        async with runner_recovery_scope(harness):
            lease = _lease_context(leased)
            # A resumed claim may already be RUNNING. Starting it again would be
            # an invalid transition and could lose the checkpoint lineage.
            current_status = str(leased.get("status") or "").upper()
            if current_status == "RUNNING":
                started = leased
            else:
                started = await self._control.start_work_unit(
                    mission_id,
                    work_unit_id,
                    runner_id=self._runner_id,
                    lease_id=lease.lease_id,
                )
                _assert_lease_context(started, lease)

            try:
                result = await self._execute_with_supervision(
                    mission_id,
                    work_unit_id,
                    lease,
                    code=code,
                    language=language,
                    timeout=timeout,
                    cwd=cwd,
                    lease_seconds=lease_seconds,
                    harness=harness,
                    resume=resume,
                )
            except asyncio.CancelledError:
                with suppress(RunnerControlError):
                    await self._fail(
                        mission_id,
                        work_unit_id,
                        lease,
                        "runner execution cancelled",
                    )
                raise
            except RunnerHeartbeatError as exc:
                await self._fail(
                    mission_id,
                    work_unit_id,
                    lease,
                    f"heartbeat supervision failed: {exc}",
                )
                raise RunnerExecutionError(
                    f"heartbeat supervision failed for WorkUnit {work_unit_id}"
                ) from exc
            except Exception as exc:
                await self._fail(
                    mission_id,
                    work_unit_id,
                    lease,
                    f"Harness execution raised: {exc}",
                )
                raise RunnerExecutionError(
                    f"Harness execution failed for WorkUnit {work_unit_id}"
                ) from exc

            if not result.success:
                reason = _execution_failure_reason(result)
                failed = await self._fail(mission_id, work_unit_id, lease, reason)
                return RunnerRunResult(
                    success=False,
                    work_unit=failed,
                    artifact=None,
                    failure_reason=reason,
                )

            try:
                published = await self._publisher.publish_bytes(result.stdout.encode())
                artifact_id = _artifact_id(work_unit_id, lease.attempt, published.digest)
                await self._control.register_artifact(
                    mission_id,
                    work_unit_id,
                    runner_id=self._runner_id,
                    lease_id=lease.lease_id,
                    artifact=published,
                    artifact_id=artifact_id,
                    kind=artifact_kind,
                    media_type=media_type,
                )
                completed = await self._control.complete_work_unit(
                    mission_id,
                    work_unit_id,
                    runner_id=self._runner_id,
                    lease_id=lease.lease_id,
                    artifact_refs=[
                        {"id": artifact_id, "digest": published.digest},
                    ],
                )
            except Exception as exc:
                await self._fail(
                    mission_id,
                    work_unit_id,
                    lease,
                    f"artifact reporting failed: {exc}",
                )
                raise RunnerExecutionError(
                    f"artifact reporting failed for WorkUnit {work_unit_id}"
                ) from exc

            return RunnerRunResult(
                success=True,
                work_unit=completed,
                artifact=published,
            )

    async def _execute_with_supervision(
        self,
        mission_id: str,
        work_unit_id: str,
        lease: _LeaseContext,
        *,
        code: str,
        language: str,
        timeout: float,
        cwd: Path | None,
        lease_seconds: int,
        harness: HarnessPort,
        resume: HarnessResumeInput | None = None,
    ) -> SandboxResult:
        execution_task = asyncio.create_task(
            harness.execute(
                HarnessRequest(
                    code=code,
                    language=language,
                    timeout=timeout,
                    cwd=cwd,
                    execution=HarnessExecutionContext(
                        mission_id=mission_id,
                        work_unit_id=work_unit_id,
                        attempt=lease.attempt,
                    ),
                    on_text_delta=self._on_text_delta,
                    resume=resume,
                    # The private journal fingerprints its actual tool workspace.
                    # Stateless Harnesses have no recoverable workspace binding.
                    workspace_revision=None,
                    context_manifest_digest=context_manifest_digest(code),
                )
            )
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(
                mission_id,
                work_unit_id,
                lease,
                lease_seconds=lease_seconds,
            )
        )
        try:
            done, _ = await asyncio.wait(
                (execution_task, heartbeat_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                heartbeat_error = heartbeat_task.exception()
                if heartbeat_error is None:
                    heartbeat_error = RunnerHeartbeatError(
                        "heartbeat supervisor stopped unexpectedly"
                    )
                execution_task.cancel()
                await _drain_cancelled_task(execution_task)
                await self._flush_supervisor_state(harness)
                if isinstance(heartbeat_error, RunnerHeartbeatError):
                    raise heartbeat_error
                raise RunnerHeartbeatError(
                    "lease heartbeat failed"
                ) from heartbeat_error
            return execution_task.result().sandbox
        except asyncio.CancelledError:
            execution_task.cancel()
            await _drain_cancelled_task(execution_task)
            await self._flush_supervisor_state(harness)
            raise
        finally:
            if not heartbeat_task.done():
                heartbeat_task.cancel()
            await _drain_cancelled_task(heartbeat_task)
            await self._flush_supervisor_state(harness)

    async def _flush_supervisor_state(self, harness: HarnessPort) -> None:
        """Flush request-scoped receipts/checkpoints before Runner exits.

        Harness implementations may expose either an async or synchronous
        ``flush_*`` hook. Missing hooks are valid for stateless test Harnesses;
        flush failures are logged but never turn a lease-loss path into a
        second uncontrolled execution.
        """
        for name in ("flush_receipts", "flush_checkpoints", "flush"):
            callback = getattr(harness, name, None)
            if not callable(callback):
                continue
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("runner supervisor state flush failed: %s", name)

    async def _heartbeat_loop(
        self,
        mission_id: str,
        work_unit_id: str,
        lease: _LeaseContext,
        *,
        lease_seconds: int,
    ) -> None:
        interval = self._heartbeat_interval_seconds
        if interval is None:
            interval = min(max(lease_seconds / 3, 0.1), 30.0)
        while True:
            await asyncio.sleep(interval)
            renewed = await self._control.heartbeat_work_unit(
                mission_id,
                work_unit_id,
                runner_id=self._runner_id,
                lease_id=lease.lease_id,
                lease_seconds=lease_seconds,
            )
            try:
                _assert_lease_context(renewed, lease)
            except RunnerControlError as exc:
                raise RunnerHeartbeatError(str(exc)) from exc

    async def _fail(
        self,
        mission_id: str,
        work_unit_id: str,
        lease: _LeaseContext,
        reason: str,
    ) -> dict[str, Any]:
        try:
            return await self._control.fail_work_unit(
                mission_id,
                work_unit_id,
                runner_id=self._runner_id,
                lease_id=lease.lease_id,
                reason=reason[:2000],
            )
        except Exception as exc:
            raise RunnerControlError(
                f"Mission Control could not record WorkUnit failure: {work_unit_id}"
            ) from exc


def _lease_context(payload: Mapping[str, Any]) -> _LeaseContext:
    lease = payload.get("lease")
    if not isinstance(lease, Mapping):
        raise RunnerControlError("Mission Control lease response has no lease")
    lease_id = lease.get("id")
    attempt = payload.get("attempt")
    if not isinstance(lease_id, str) or not lease_id:
        raise RunnerControlError("Mission Control lease response has no lease id")
    if type(attempt) is not int or attempt < 1:
        raise RunnerControlError("Mission Control lease response has no attempt")
    return _LeaseContext(lease_id=lease_id, attempt=attempt)


def assert_claimed_work_unit(
    payload: Mapping[str, Any],
    *,
    mission_id: str,
    runner_id: str,
    agent_id: str,
    adapter_type: str,
) -> None:
    if payload.get("missionId") != mission_id:
        raise RunnerControlError("Mission Control returned a WorkUnit for another mission")
    if payload.get("status") not in {"LEASED", "RUNNING"}:
        raise RunnerControlError("Mission Control claim did not return a LEASED or RUNNING WorkUnit")
    if payload.get("assignedAgentId") != agent_id:
        raise RunnerControlError("Mission Control returned a WorkUnit for another agent")
    if payload.get("assignedAdapter") != adapter_type:
        raise RunnerControlError("Mission Control returned a WorkUnit for another adapter")
    lease = payload.get("lease")
    if not isinstance(lease, Mapping) or lease.get("runnerId") != runner_id:
        raise RunnerControlError("Mission Control claim lease belongs to another runner")


def _require_running_resume(payload: Mapping[str, Any], execution: Any) -> None:
    if payload.get("status") != "RUNNING":
        return
    if (
        isinstance(execution, ClaimedWorkExecution)
        and isinstance(execution.execution_input, RunnerExecutionInput)
        and isinstance(execution.execution_input.resume, HarnessResumeInput)
        and execution.execution_input.resume.attempt == payload.get("attempt")
    ):
        return
    close = getattr(getattr(execution, "harness", None), "close_recovery", None)
    if callable(close):
        close()
    raise ClaimedWorkResolutionError("RUNNING WorkUnit requires a validated resume journal")


def parse_workspace_claim_status(
    claimed_payload: Mapping[str, Any],
) -> WorkspaceClaimStatus:
    if "claimStatus" not in claimed_payload or "workUnit" not in claimed_payload:
        raise RunnerControlError(
            "Mission Control returned an incomplete workspace claim response"
        )
    try:
        status = WorkspaceClaimStatus(claimed_payload["claimStatus"])
    except (TypeError, ValueError) as exc:
        raise RunnerControlError(
            "Mission Control returned an invalid workspace claim status"
        ) from exc
    has_work_unit = claimed_payload["workUnit"] is not None
    if has_work_unit != (status == WorkspaceClaimStatus.CLAIMED):
        raise RunnerControlError(
            "Mission Control returned an inconsistent workspace claim response"
        )
    return status


def _assert_lease_context(payload: Mapping[str, Any], expected: _LeaseContext) -> None:
    actual = _lease_context(payload)
    if actual != expected:
        raise RunnerControlError("Mission Control changed the WorkUnit lease")


def _execution_failure_reason(result: SandboxResult) -> str:
    if result.error:
        return result.error
    if result.stderr:
        return result.stderr
    return f"sandbox exited with code {result.exit_code}"


def _artifact_id(work_unit_id: str, attempt: int, digest: str) -> str:
    digest_hex = digest.removeprefix("sha256:")
    return f"artifact-{work_unit_id}-{attempt}-{digest_hex[:32]}"


async def _drain_cancelled_task(task: asyncio.Task[Any]) -> None:
    """Wait for a supervised task after cancellation without leaking it."""
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception:  # noqa: BLE001 - task is drained after its outcome is captured
        # The caller has already captured the relevant supervision failure.
        # Drain the task so asyncio does not report an unhandled exception.
        return


__all__ = [
    "A2AInboundClaimedWorkResolver",
    "ClaimedHarnessFactoryPort",
    "ClaimedWorkExecution",
    "ClaimedWorkResolutionError",
    "KindAwareClaimedWorkResolver",
    "MissionControlRunnerClient",
    "MissionControlRunnerPort",
    "MissionForkClaimedWorkResolver",
    "DesktopTaskClaimedWorkResolver",
    "RunnerControlError",
    "RunnerError",
    "RunnerExecutionError",
    "RunnerHeartbeatError",
    "RunnerRunResult",
    "RunnerWorkspacePollResult",
    "SandboxPort",
    "WorkUnitRunner",
    "assert_claimed_work_unit",
    "compile_mission_fork_context",
    "parse_workspace_claim_status",
    "run_mission_sync",
]


# ── SWE-bench / benchmark sync entry ─────────────────────────────────
