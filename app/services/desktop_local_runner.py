"""In-process desktop local runner (R1).

When ``AGENTHUB_DESKTOP_LOCAL_RUNNER=1`` the Mission Control process starts
a :class:`DesktopLocalRunnerController` (see
docs/internal/architecture/desktop-local-runner-plan.md §2):

- a :class:`RunnerWorker` polls workspace ``local-admin`` through the
  self-hosted HTTP API with an authenticated admin token, keeping the
  "the executor never owns state" boundary intact;
- claimed ``desktop.task`` WorkUnits execute through the function-calling
  Harness with the model configured in the admin model table and the fixed
  desktop file-tool whitelist;
- a derivation loop creates exactly one ``desktop.task`` root WorkUnit per
  RUNNING manual Mission so desktop-created Missions become claimable;
- an unattended verification loop discovers VERIFYING items through the
  Mission Control verifier API, checks the registered Artifact bytes and
  submits PASS/FAIL Evidence so deterministic missions finish on their own.

The whole controller is env-gated and defaults to off; production and
server deployments never construct it.
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
    ActorRef,
    ActorType,
    ArtifactRef,
    EvidenceVerdict,
    MissionSourceType,
    MissionStatus,
    OutputSpec,
)
from app.repositories import MissionRepository
from app.services.artifact_integrity_service import (
    ArtifactByteVerificationError,
    ArtifactByteVerifier,
    build_artifact_byte_verifier,
)
from app.services.artifact_store_service import ArtifactPublisher
from app.services.desktop_runner_tools import (
    DelegateSubtaskConfig,
    build_desktop_runner_tools,
)
from app.services.harness_checkpoint import (
    HarnessCheckpointPort,
    HarnessExecutionContext,
)
from app.services.harness_service import (
    FunctionCallingHarness,
    FunctionTool,
    HarnessPort,
    ModelPort,
)
from app.services.mission_service import (
    DESKTOP_TASK_WORK_UNIT_KIND,
    MissionService,
)
from app.services.model_port import (
    DEFAULT_CONTEXT_CHAR_BUDGET,
    ModelAdapterPort,
    build_function_tool_schemas,
)
from app.services.runner_checkpoint import MissionControlHarnessCheckpointFactory
from app.services.runner_composition import (
    CapabilityBindingFactoryPort,
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
from app.services.workspace_context import build_workspace_root

logger = logging.getLogger("agenthub.desktop_local_runner")

ENABLE_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER"
BASE_URL_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_BASE_URL"
ADMIN_NAME_ENV = "AGENTHUB_DESKTOP_ADMIN_NAME"
ADMIN_PASSWORD_ENV = "AGENTHUB_DESKTOP_ADMIN_PASSWORD"
TOKEN_ENV = "AGENTHUB_DESKTOP_RUNNER_TOKEN"
TOKEN_FILE_ENV = "AGENTHUB_DESKTOP_RUNNER_TOKEN_FILE"
USER_ID_ENV = "AGENTHUB_DESKTOP_RUNNER_USER_ID"
WORKSPACE_ROOT_ENV = "AGENTHUB_DESKTOP_WORKSPACE_ROOT"
MODEL_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL"
MODEL_BASE_URL_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL_BASE_URL"
PROVIDER_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_PROVIDER"
VERIFY_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY"
VERIFY_INTERVAL_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY_INTERVAL_SECONDS"
CONTEXT_CHAR_BUDGET_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_CONTEXT_CHAR_BUDGET"

# The desktop shell always talks to the local Mission Control workspace.
DESKTOP_WORKSPACE_ID = "local-admin"
DESKTOP_AGENT_ID = "local-desktop-agent"
DESKTOP_ADAPTER_TYPE = "function-calling"
DESKTOP_RUNNER_LABEL = "desktop-local-runner"
DESKTOP_SYSTEM_PROMPT = (
    "你是桌面本地任务执行器。使用提供的文件工具在桌面工作区内完成任务，"
    "不要操作工作区之外的路径，完成后用一句话总结结果。"
)

# Unattended verification identity (submitted as Evidence verifier ref).
DESKTOP_VERIFIER_ID = "desktop-local-verifier"
DESKTOP_VERIFIER_VERSION = "1"
_DESKTOP_EVALUATOR = "artifact-set.v1"

_DEFAULT_BASE_URL = "http://127.0.0.1:28000"
_DEFAULT_MAX_ITERATIONS = 8
_DEFAULT_MAX_TOOL_CALLS = 32
_DEFAULT_MAX_TOTAL_TOKENS = 200_000
_DEFAULT_CONTEXT_CHAR_BUDGET = DEFAULT_CONTEXT_CHAR_BUDGET
_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_LEASE_SECONDS = 300
_DEFAULT_IDLE_DELAY_SECONDS = 0.5
_DEFAULT_MAX_DELAY_SECONDS = 10.0
_DEFAULT_DERIVATION_INTERVAL_SECONDS = 5.0
_DEFAULT_VERIFY_INTERVAL_SECONDS = 5.0


class DesktopRunnerError(RuntimeError):
    """Raised when the desktop local runner cannot be composed or started."""


@dataclass(frozen=True)
class DesktopLocalRunnerSettings:
    """Env-derived, fail-closed desktop runner configuration."""

    enabled: bool
    base_url: str
    admin_name: str
    admin_password: str
    token: str | None
    token_file: str | None
    user_id: str | None
    workspace_id: str
    workspace_root: Path | None
    model_name: str | None
    max_iterations: int
    max_tool_calls: int
    max_total_tokens: int
    timeout_seconds: float
    lease_seconds: int
    idle_delay_seconds: float
    max_delay_seconds: float
    derivation_interval_seconds: float
    verify_enabled: bool
    verify_interval_seconds: float
    context_char_budget: int = _DEFAULT_CONTEXT_CHAR_BUDGET

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DesktopLocalRunnerSettings:
        environment = os.environ if env is None else env
        enabled = environment.get(ENABLE_ENV, "").strip() == "1"
        base_url = (
            environment.get(BASE_URL_ENV, "").strip() or _DEFAULT_BASE_URL
        ).rstrip("/")
        workspace_root_value = environment.get(WORKSPACE_ROOT_ENV, "").strip()
        model_name = environment.get(MODEL_ENV, "").strip() or None
        token = environment.get(TOKEN_ENV, "").strip() or None
        token_file = environment.get(TOKEN_FILE_ENV, "").strip() or None
        user_id = environment.get(USER_ID_ENV, "").strip() or None
        if token_file is not None and user_id is None:
            raise DesktopRunnerError(
                f"{TOKEN_FILE_ENV} requires {USER_ID_ENV} to bind the Runner identity"
            )
        if token is not None and user_id is None:
            raise DesktopRunnerError(
                f"{TOKEN_ENV} requires {USER_ID_ENV} to bind the Runner identity"
            )
        max_iterations = _positive_int(
            environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_ITERATIONS",
            _DEFAULT_MAX_ITERATIONS,
        )
        max_tool_calls = _positive_int(
            environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOOL_CALLS",
            _DEFAULT_MAX_TOOL_CALLS,
        )
        max_total_tokens = _positive_int(
            environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOTAL_TOKENS",
            _DEFAULT_MAX_TOTAL_TOKENS,
        )
        context_char_budget = _positive_int(
            environment, CONTEXT_CHAR_BUDGET_ENV,
            _DEFAULT_CONTEXT_CHAR_BUDGET,
        )
        timeout_seconds = _positive_float(
            environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_TIMEOUT_SECONDS",
            _DEFAULT_TIMEOUT_SECONDS,
        )
        return cls(
            enabled=enabled,
            base_url=base_url,
            admin_name=environment.get(ADMIN_NAME_ENV, "").strip() or "admin",
            admin_password=environment.get(ADMIN_PASSWORD_ENV, "").strip()
            or "admin123",
            token=token,
            token_file=token_file,
            user_id=user_id,
            workspace_id=DESKTOP_WORKSPACE_ID,
            workspace_root=Path(workspace_root_value) if workspace_root_value else None,
            model_name=model_name,
            max_iterations=max_iterations,
            max_tool_calls=max_tool_calls,
            max_total_tokens=max_total_tokens,
            timeout_seconds=timeout_seconds,
            lease_seconds=_positive_int(
                environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_LEASE_SECONDS",
                _DEFAULT_LEASE_SECONDS,
            ),
            idle_delay_seconds=_positive_float(
                environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_IDLE_DELAY_SECONDS",
                _DEFAULT_IDLE_DELAY_SECONDS,
            ),
            max_delay_seconds=_positive_float(
                environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_DELAY_SECONDS",
                _DEFAULT_MAX_DELAY_SECONDS,
            ),
            derivation_interval_seconds=_positive_float(
                environment,
                "AGENTHUB_DESKTOP_LOCAL_RUNNER_DERIVATION_INTERVAL_SECONDS",
                _DEFAULT_DERIVATION_INTERVAL_SECONDS,
            ),
            # Unattended verification defaults to on; ``0`` opts out.
            verify_enabled=environment.get(VERIFY_ENV, "1").strip() == "1",
            verify_interval_seconds=_positive_float(
                environment,
                VERIFY_INTERVAL_ENV,
                _DEFAULT_VERIFY_INTERVAL_SECONDS,
            ),
            context_char_budget=context_char_budget,
        )

    def default_workspace_root(self) -> Path:
        """Return the runner workspace root under the standard workspace tree."""
        if self.workspace_root is not None:
            return self.workspace_root
        return build_workspace_root(DESKTOP_WORKSPACE_ID, DESKTOP_RUNNER_LABEL)


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 1:
        raise DesktopRunnerError(f"{key} must be positive")
    return value


def _positive_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value <= 0:
        raise DesktopRunnerError(f"{key} must be positive")
    return value


# ── Model composition ────────────────────────────────────────────────────


@dataclass(frozen=True)
class DesktopModelConfig:
    """One admin-configured model resolved for the desktop runner."""

    provider: str
    model: str
    api_key: str
    base_url: str


class DesktopModelConfigLoader(Protocol):
    async def __call__(self, model_name: str | None) -> DesktopModelConfig: ...


async def load_default_model_config(
    model_name: str | None = None,
) -> DesktopModelConfig:
    """Read the newest active admin model configuration from the database.

    The API key is decrypted with the same secret mechanism
    ``/api/admin/models`` writes with; the desktop-injected
    ``AGENTHUB_DESKTOP_MODEL_API_KEY`` fills in when a configuration has no
    key of its own.
    """
    from app.db.session import afetch_all
    from app.services.secret_service import decrypt_secret

    rows = await afetch_all(
        "SELECT provider, model_name, api_key, base_url FROM model_configs "
        "WHERE is_active = 1 ORDER BY id DESC"
    )
    selected: Mapping[str, Any] | None = None
    for row in rows:
        if model_name is None or row.get("model_name") == model_name:
            selected = row
            break
    key_env = os.environ.get("AGENTHUB_DESKTOP_MODEL_API_KEY", "")
    model_env = os.environ.get(MODEL_ENV, "").strip()
    base_url_env = os.environ.get(MODEL_BASE_URL_ENV, "").strip()
    provider_env = os.environ.get(PROVIDER_ENV, "").strip()
    if selected is None:
        # Pure-environment fallback: no admin model configuration rows yet,
        # so the desktop-injected key/model/base URL define the provider.
        if not key_env or not model_env:
            raise DesktopRunnerError(
                "no active admin model configuration is available for the "
                "desktop local runner"
            )
        return DesktopModelConfig(
            provider=provider_env or "openai",
            model=model_env,
            api_key=key_env,
            base_url=base_url_env,
        )
    api_key = decrypt_secret(str(selected.get("api_key") or ""))
    if not api_key:
        api_key = key_env
    return DesktopModelConfig(
        provider=str(selected.get("provider") or "") or provider_env or "openai",
        model=str(selected.get("model_name") or "") or model_env,
        api_key=api_key,
        base_url=str(selected.get("base_url") or "") or base_url_env,
    )


class DesktopModelFactory(HarnessModelFactoryPort):
    """Build a request-scoped ModelPort from the admin model configuration."""

    def __init__(
        self,
        config: DesktopModelConfig,
        *,
        context_char_budget: int = DEFAULT_CONTEXT_CHAR_BUDGET,
    ) -> None:
        if not config.model.strip():
            raise DesktopRunnerError(
                "desktop model configuration has an empty model name"
            )
        if context_char_budget < 1:
            raise DesktopRunnerError("context_char_budget must be positive")
        self._config = config
        self._context_char_budget = context_char_budget
        from app.services.adapter_manager import adapter_manager

        self._adapter = adapter_manager.get_adapter(config.provider)

    def build(self, tools: Sequence[FunctionTool]) -> ModelPort:
        return ModelAdapterPort(
            self._adapter,
            model=self._config.model,
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            system_prompt=DESKTOP_SYSTEM_PROMPT,
            tools=build_function_tool_schemas(list(tools)),
            context_char_budget=self._context_char_budget,
        )


class _NoCapabilityBindings(CapabilityBindingFactoryPort):
    """The desktop whitelist is bound directly, not through capabilities."""

    def build(self, execution: HarnessExecutionContext) -> Sequence[Any]:
        del execution
        return []


class DesktopTaskHarnessFactory:
    """Build the request-scoped Harness for one claimed desktop task."""

    def __init__(
        self,
        model_factory: HarnessModelFactoryPort,
        *,
        tools: Sequence[FunctionTool],
        checkpoint_factory: Any | None = None,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS,
        max_total_tokens: int | None = _DEFAULT_MAX_TOTAL_TOKENS,
        max_model_cost: float | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        self._model_factory = model_factory
        self._tools = list(tools)
        self._checkpoint_factory = checkpoint_factory
        self._max_iterations = max_iterations
        self._max_tool_calls = max_tool_calls
        self._max_total_tokens = max_total_tokens
        self._max_model_cost = max_model_cost

    def build(self, context: Mapping[str, Any]) -> HarnessPort:
        work_unit = context.get("workUnit")
        mission = context.get("mission")
        if not isinstance(work_unit, Mapping) or not isinstance(mission, Mapping):
            raise TypeError("desktop task execution context is incomplete")
        execution = HarnessExecutionContext(
            mission_id=str(mission.get("id", "")),
            work_unit_id=str(work_unit.get("id", "")),
            attempt=int(work_unit.get("attempt", 0)),
        )
        checkpoint_port: HarnessCheckpointPort | None = None
        if self._checkpoint_factory is not None:
            lease = work_unit.get("lease")
            lease_id = lease.get("id") if isinstance(lease, Mapping) else None
            if not isinstance(lease_id, str) or not lease_id:
                raise ValueError("desktop task context has no lease id")
            checkpoint_port = self._checkpoint_factory.build(
                execution,
                lease_id=lease_id,
            )
        model = self._model_factory.build(self._tools)
        model_cost_limit = self._max_model_cost
        contract = context.get("contract")
        if isinstance(contract, Mapping):
            budgets = contract.get("budgets")
            if isinstance(budgets, Mapping):
                contract_cost = budgets.get("modelCost")
                if isinstance(contract_cost, (int, float)) and contract_cost >= 0:
                    model_cost_limit = (
                        min(float(contract_cost), model_cost_limit)
                        if model_cost_limit is not None
                        else float(contract_cost)
                    )
        return FunctionCallingHarness(
            model,
            self._tools,
            max_iterations=self._max_iterations,
            max_tool_calls=self._max_tool_calls,
            max_total_tokens=self._max_total_tokens,
            max_model_cost=model_cost_limit,
            checkpoint_port=checkpoint_port,
        )


# ── Mission → WorkUnit derivation ────────────────────────────────────────


class DesktopMissionSourcePort(Protocol):
    """The durable Mission projections the derivation needs."""

    async def running_manual_missions(self, workspace_id: str) -> Sequence[Any]: ...

    async def has_work_unit_kind(self, mission_id: str, kind: str) -> bool: ...

    async def create_desktop_task_work_unit(self, mission_id: str) -> str: ...


class MissionControlDesktopMissionSource:
    """Derivation adapter over the in-process Mission repository."""

    def __init__(self, repository_factory: Any = MissionRepository) -> None:
        self._repository_factory = repository_factory

    async def running_manual_missions(self, workspace_id: str) -> Sequence[Any]:
        repository = self._repository_factory()
        missions = await repository.list_missions(workspace_id, limit=200)
        return [
            mission
            for mission in missions
            if mission.status == MissionStatus.RUNNING
            and mission.source.type == MissionSourceType.MANUAL
        ]

    async def has_work_unit_kind(self, mission_id: str, kind: str) -> bool:
        repository = self._repository_factory()
        work_units = await repository.list_work_units(mission_id)
        return any(unit.kind == kind for unit in work_units)

    async def create_desktop_task_work_unit(self, mission_id: str) -> str:
        repository = self._repository_factory()
        service = MissionService(repository)
        work_unit = await service.create_work_unit(
            mission_id,
            work_unit_id=None,
            kind=DESKTOP_TASK_WORK_UNIT_KIND,
            dependencies=[],
            input_refs=[],
            expected_outputs=[OutputSpec(kind="text", required=False)],
            required_capabilities=[],
            assigned_adapter=DESKTOP_ADAPTER_TYPE,
            actor=ActorRef(
                type=ActorType.SERVICE,
                id=DESKTOP_RUNNER_LABEL,
                display_name="Desktop Local Runner",
            ),
            assigned_agent_id=DESKTOP_AGENT_ID,
        )
        return work_unit.id


async def derive_desktop_task_work_units(
    mission_source: DesktopMissionSourcePort,
    *,
    workspace_id: str,
) -> list[str]:
    """Create exactly one ``desktop.task`` WorkUnit per eligible Mission.

    A Mission is eligible when it is RUNNING, desktop-created (manual
    source) and has no ``desktop.task`` WorkUnit yet — including failed
    ones, so derivation never retries doomed Missions on its own.
    """
    derived: list[str] = []
    for mission in await mission_source.running_manual_missions(workspace_id):
        if await mission_source.has_work_unit_kind(
            str(mission.id), DESKTOP_TASK_WORK_UNIT_KIND
        ):
            continue
        work_unit_id = await mission_source.create_desktop_task_work_unit(
            str(mission.id)
        )
        logger.info(
            "desktop runner derived WorkUnit %s for Mission %s",
            work_unit_id,
            mission.id,
        )
        derived.append(work_unit_id)
    return derived


# ── Authentication ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class DesktopRunnerIdentity:
    access_token: str
    user_id: str


class DesktopAuthenticator:
    """Resolve the Runner identity through the existing token mechanisms."""

    def __init__(self, client_factory: Any = httpx.AsyncClient) -> None:
        self._client_factory = client_factory

    async def resolve(
        self,
        settings: DesktopLocalRunnerSettings,
    ) -> DesktopRunnerIdentity:
        if settings.token_file is not None:
            token = Path(settings.token_file).read_text(encoding="utf-8").strip()
            if not token:
                raise DesktopRunnerError("desktop runner token file is empty")
            assert settings.user_id is not None
            return DesktopRunnerIdentity(
                access_token=token,
                user_id=settings.user_id,
            )
        if settings.token is not None:
            assert settings.user_id is not None
            return DesktopRunnerIdentity(
                access_token=settings.token,
                user_id=settings.user_id,
            )
        return await self._login(settings)

    async def _login(
        self,
        settings: DesktopLocalRunnerSettings,
    ) -> DesktopRunnerIdentity:
        async with self._client_factory() as client:
            response = await client.post(
                f"{settings.base_url}/api/auth/login",
                json={"name": settings.admin_name, "password": settings.admin_password},
            )
        if response.is_error:
            raise DesktopRunnerError(
                f"desktop runner login failed with HTTP {response.status_code}"
            )
        payload = response.json()
        token = payload.get("accessToken") if isinstance(payload, Mapping) else None
        user = payload.get("user") if isinstance(payload, Mapping) else None
        user_id = str(user.get("id", "")) if isinstance(user, Mapping) else ""
        if not isinstance(token, str) or not token or not user_id:
            raise DesktopRunnerError("desktop runner login returned no identity")
        return DesktopRunnerIdentity(access_token=token, user_id=user_id)


# ── Unattended verification ──────────────────────────────────────────────


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
        self._verifier: DesktopVerifierControlPort | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._worker: RunnerWorker | None = None
        self._worker_task: asyncio.Task[Any] | None = None
        self._derivation_task: asyncio.Task[Any] | None = None
        self._verification_task: asyncio.Task[Any] | None = None
        self._stop_event = asyncio.Event()
        self._runner: WorkUnitRunner | None = None

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    async def start(self) -> None:
        if self._worker_task is not None:
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
        self._verifier = verifier

        model_factory = self._injected_model_factory
        if model_factory is None:
            model_factory = DesktopModelFactory(
                await load_default_model_config(settings.model_name),
                context_char_budget=settings.context_char_budget,
            )

        if self._tools is not None:
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
            )

        self._runner = self._build_runner(
            control,
            runner_id=runner_id,
            model_factory=model_factory,
            tools=tools,
        )
        worker = RunnerWorker(
            self._runner,
            workspace_id=settings.workspace_id,
            lease_seconds=settings.lease_seconds,
            idle_delay_seconds=settings.idle_delay_seconds,
            max_delay_seconds=settings.max_delay_seconds,
        )
        self._worker = worker
        self._derivation_task = asyncio.create_task(self._derivation_loop())
        self._worker_task = asyncio.create_task(worker.run())
        if settings.verify_enabled and self._verifier is not None:
            self._verification_task = asyncio.create_task(self._verification_loop())
        logger.info(
            "desktop local runner started: workspace=%s root=%s runner=%s verify=%s",
            settings.workspace_id,
            self._workspace_root,
            runner_id,
            "on" if self._verification_task is not None else "off",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker is not None:
            self._worker.request_stop()
        for task in (self._worker_task, self._derivation_task, self._verification_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker_task = None
        self._derivation_task = None
        self._verification_task = None
        self._worker = None
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
        if failure_reason is None:
            verdict = EvidenceVerdict.PASS
            summary = (
                f"{_DESKTOP_EVALUATOR} verified {len(artifacts)} Artifact(s): "
                "files exist, are non-empty and byte digests match."
            )
        else:
            verdict = EvidenceVerdict.FAIL
            summary = f"desktop artifact check failed: {failure_reason}"
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
    controller = getattr(app.state, "desktop_local_runner", None)
    if controller is None:
        return
    await controller.stop()
    app.state.desktop_local_runner = None


__all__ = [
    "CONTEXT_CHAR_BUDGET_ENV",
    "DESKTOP_ADAPTER_TYPE",
    "DESKTOP_AGENT_ID",
    "DESKTOP_TASK_WORK_UNIT_KIND",
    "DESKTOP_VERIFIER_ID",
    "DESKTOP_VERIFIER_VERSION",
    "DESKTOP_WORKSPACE_ID",
    "VERIFY_ENV",
    "VERIFY_INTERVAL_ENV",
    "DesktopAuthenticator",
    "DesktopLocalRunnerController",
    "DesktopLocalRunnerSettings",
    "DesktopModelConfig",
    "DesktopModelFactory",
    "DesktopRunnerError",
    "DesktopTaskHarnessFactory",
    "DesktopVerifierControlPort",
    "MissionControlDesktopMissionSource",
    "derive_desktop_task_work_units",
    "desktop_local_runner_settings",
    "load_default_model_config",
    "shutdown_desktop_local_runner",
    "startup_desktop_local_runner",
]
