"""Runner transport/execution ports and immutable result values."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.services.artifact_store_service import PublishedArtifact
from app.services.harness_service import HarnessPort, HarnessResumeInput
from app.services.tools.sandbox_executor import SandboxResult
from app.services.workspace_admission_service import WorkspaceClaimStatus

class RunnerError(RuntimeError):
    """Base error for a Runner execution attempt."""


class RunnerControlError(RunnerError):
    """Raised when Mission Control rejects or cannot complete a command."""


class RunnerExecutionError(RunnerError):
    """Raised when execution or Artifact publication cannot finish honestly."""


class RunnerHeartbeatError(RunnerControlError):
    """Raised when lease supervision cannot renew the active lease."""


class ClaimedWorkResolutionError(RunnerExecutionError):
    """Raised when durable claimed context cannot be compiled safely."""


class MissionControlRunnerPort(Protocol):
    async def claim_ready_work_unit(
        self,
        workspace_id: str,
        *,
        runner_id: str,
        agent_id: str,
        adapter_type: str,
        supported_work_unit_kinds: tuple[str, ...],
        lease_seconds: int,
        supported_capabilities: tuple[str, ...] = (),
        resume_mission_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def claim_work_unit(
        self,
        mission_id: str,
        *,
        runner_id: str,
        agent_id: str,
        adapter_type: str,
        lease_seconds: int,
    ) -> dict[str, Any]: ...

    async def lease_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_seconds: int,
    ) -> dict[str, Any]: ...

    async def get_execution_context(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
    ) -> dict[str, Any]: ...

    async def start_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
    ) -> dict[str, Any]: ...

    async def heartbeat_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        lease_seconds: int,
    ) -> dict[str, Any]: ...

    async def record_execution_checkpoint(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        checkpoint_id: str,
        sequence: int,
        phase: str,
        iteration: int,
        tool_calls: int,
        prompt_tokens: int,
        completion_tokens: int,
        model_cost: float,
        terminal: bool,
        failure_reason: str | None,
        tool_name: str | None = None,
        tool_success: bool | None = None,
        resume_protocol_version: int | None = None,
        next_action: dict[str, object] | None = None,
        idempotency_key: str | None = None,
        workspace_revision: str | None = None,
        context_manifest_digest: str | None = None,
    ) -> dict[str, Any]: ...

    async def register_artifact(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        artifact: PublishedArtifact,
        artifact_id: str,
        kind: str,
        media_type: str,
    ) -> dict[str, Any]: ...

    async def complete_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        artifact_refs: list[dict[str, str]],
    ) -> dict[str, Any]: ...

    async def fail_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        reason: str,
    ) -> dict[str, Any]: ...


class SandboxPort(Protocol):
    async def execute(
        self,
        code: str,
        language: str = "python",
        timeout: float = 30.0,
        cwd: str | None = None,
    ) -> SandboxResult: ...


@dataclass(frozen=True)
class RunnerExecutionInput:
    """Resolver output for a claimed WorkUnit.

    The resolver is the trust boundary that turns durable WorkUnit references
    into bounded executable input. A Runner never infers code from references.
    """

    code: str
    language: str = "python"
    timeout: float = 30.0
    cwd: Path | None = None
    resume: HarnessResumeInput | None = None


@dataclass(frozen=True)
class ClaimedWorkExecution:
    """Lease-fenced input and the request-scoped Harness that may execute it."""

    execution_input: RunnerExecutionInput
    harness: HarnessPort


class ClaimedHarnessFactoryPort(Protocol):
    def build(self, context: Mapping[str, Any]) -> HarnessPort: ...


class ClaimedWorkResolver(Protocol):
    async def resolve(
        self,
        work_unit: Mapping[str, Any],
    ) -> ClaimedWorkExecution: ...


@dataclass(frozen=True)
class RunnerRunResult:
    success: bool
    work_unit: dict[str, Any]
    artifact: PublishedArtifact | None
    failure_reason: str | None = None


@dataclass(frozen=True)
class RunnerWorkspacePollResult:
    """One workspace poll with its low-cardinality admission outcome."""

    claim_status: WorkspaceClaimStatus
    run_result: RunnerRunResult | None

    def __post_init__(self) -> None:
        has_run_result = self.run_result is not None
        if has_run_result != (self.claim_status == WorkspaceClaimStatus.CLAIMED):
            raise ValueError("claim status and Runner result are inconsistent")


@dataclass(frozen=True)
class _LeaseContext:
    lease_id: str
    attempt: int
