from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from app.services.harness_checkpoint import HarnessError, HarnessExecutionContext
from app.services.model_contract import ModelUsage, ToolCall, ToolResult
from app.services.tools.sandbox_executor import SandboxResult


@dataclass(frozen=True)
class HarnessRequest:
    """Request-scoped input for one bounded Harness execution."""

    code: str
    language: str
    timeout: float
    cwd: Path | None = None
    execution: HarnessExecutionContext | None = None
    on_text_delta: Callable[[str], Awaitable[None] | None] | None = None
    workspace_revision: str | None = None
    context_manifest_digest: str | None = None
    resume: HarnessResumeInput | None = None

@dataclass(frozen=True)
class HarnessResumeInput:
    """Durable execution state used to re-enter a Harness turn safely.

    ``recovered_tool_results`` contains only canonical, content-bounded tool
    results whose receipts were already reconciled by the Runner.  A Harness
    never reconstructs a side effect from this object and therefore cannot
    accidentally replay a tool during resume.
    """

    checkpoint_id: str
    attempt: int
    next_action: Mapping[str, object] | None = None
    recovered_tool_results: tuple[ToolResult, ...] = ()
    start_iteration: int = 0
    usage: ModelUsage = field(default_factory=ModelUsage)
    tool_calls: int = 0
    pending_tool_calls: tuple[ToolCall, ...] = ()
    response_content: str | None = None
    reserved_call_id: str | None = None
    checkpoint_sequence: int = 0
    phase: str = ""
    elapsed_seconds: float = 0.0
    next_iteration: int | None = None
    deadline_epoch: float | None = None

    def __post_init__(self) -> None:
        if not self.checkpoint_id:
            raise ValueError("resume checkpoint_id must be non-empty")
        if isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError("resume attempt must be a positive integer")
        if self.start_iteration < 0:
            raise ValueError("resume start_iteration must be non-negative")
        if self.next_action is not None and not isinstance(self.next_action, Mapping):
            raise TypeError("resume next_action must be an object")
        counters = (self.start_iteration, self.tool_calls, self.checkpoint_sequence)
        if any(type(value) is not int or value < 0 for value in counters):
            raise ValueError("resume counters must be non-negative integers")
        if self.reserved_call_id is not None and (
            not self.pending_tool_calls or self.pending_tool_calls[0].id != self.reserved_call_id
        ):
            raise ValueError("reserved call must be the first pending tool call")

@dataclass(frozen=True)
class HarnessResult:
    """Execution output plus loop metadata owned by the Harness."""

    sandbox: SandboxResult
    iterations: int = 1
    tool_calls: int = 0
    usage: ModelUsage = field(default_factory=ModelUsage)

class RepairErrorType(StrEnum):
    """Stable error classes eligible for a bounded repair attempt."""

    SYNTAX = "syntax"
    DEPENDENCY = "dependency"
    TEST_FAILURE = "test_failure"
    PERMISSION = "permission"
    TIMEOUT = "timeout"
    WORKSPACE_CONFLICT = "workspace_conflict"
    UNKNOWN = "unknown"

class RepairAttemptState(StrEnum):
    """Terminally observable states for one repair transaction."""

    PENDING = "pending"
    ROOT_CAUSE_EXTRACTED = "root_cause_extracted"
    PLAN_READY = "plan_ready"
    APPLYING = "applying"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    BUDGET_EXHAUSTED = "budget_exhausted"

@dataclass(frozen=True)
class RepairAttempt:
    """Bounded repair state; no implicit retries or side effects are hidden."""

    attempt: int
    error_type: RepairErrorType
    state: RepairAttemptState = RepairAttemptState.PENDING
    root_cause: str = ""
    plan: str = ""
    token_budget: int = 0
    tokens_used: int = 0
    time_budget_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    side_effect_budget: int = 0
    side_effects_used: int = 0
    message: str = ""

    def transition(self, state: RepairAttemptState, **changes: Any) -> RepairAttempt:
        if state is RepairAttemptState.APPLYING and not self.plan:
            raise ValueError("repair plan is required before applying changes")
        return replace(self, state=state, **changes)

    @property
    def budget_exhausted(self) -> bool:
        return (
            (self.token_budget > 0 and self.tokens_used > self.token_budget)
            or (self.time_budget_seconds > 0 and self.elapsed_seconds >= self.time_budget_seconds)
            or (self.side_effect_budget > 0 and self.side_effects_used >= self.side_effect_budget)
        )

Verifier = Callable[[HarnessResult], Awaitable[bool] | bool]

RepairPlanner = Callable[[RepairAttempt, HarnessResult], Awaitable[str | None] | str | None]

RepairApplier = Callable[[RepairAttempt], Awaitable[HarnessResult] | HarnessResult]

RepairRollback = Callable[[RepairAttempt], Awaitable[None] | None]

FunctionHandler = Callable[[Mapping[str, Any]], Awaitable[str]]

ArgumentValidator = Callable[[Mapping[str, Any]], Mapping[str, Any]]

@dataclass(frozen=True)
class FunctionTool:
    """A capability-granted function available for one Harness execution."""

    name: str
    handler: FunctionHandler
    validate_arguments: ArgumentValidator
    description: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)

class SandboxPort(Protocol):
    async def execute(
        self,
        code: str,
        language: str = "python",
        timeout: float = 30.0,
        cwd: str | None = None,
    ) -> SandboxResult: ...

class HarnessPort(Protocol):
    """Replaceable model/tool loop boundary used by Runner."""

    async def execute(self, request: HarnessRequest) -> HarnessResult: ...

class SandboxHarness:
    """Minimal Harness implementation backed by the isolated Sandbox port.

    It deliberately performs one bounded execution. Model calls, function
    calling, tools, checkpoints, and retries can be added behind this contract
    without giving Runner a second execution state machine.
    """

    def __init__(self, sandbox: SandboxPort) -> None:
        self._sandbox = sandbox

    async def execute(self, request: HarnessRequest) -> HarnessResult:
        if request.timeout <= 0:
            raise HarnessError("Harness timeout must be positive")
        result = await self._sandbox.execute(
            request.code,
            language=request.language,
            timeout=request.timeout,
            cwd=str(request.cwd) if request.cwd is not None else None,
        )
        return HarnessResult(sandbox=result)
