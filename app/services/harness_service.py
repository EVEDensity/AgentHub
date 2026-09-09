from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.services.harness_checkpoint import (
    HarnessCheckpoint,
    HarnessCheckpointPort,
    HarnessError,
    HarnessEvent,
    HarnessEventType,
    HarnessExecutionContext,
    InMemoryHarnessCheckpointPort,
    _HarnessRecorder,
)
from app.services.tools.sandbox_executor import SandboxResult
from app.services.model_contract import (
    Message,
    ModelPort,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelUsage,
    ToolCall,
    ToolResult,
)

# Backwards-compatible import names. These are aliases of the canonical DTOs,
# not a second protocol owned by Harness.
FunctionCall = ToolCall
FunctionResult = ToolResult

logger = logging.getLogger("agenthub.harness")

# G6 transient-error retry: one bounded backoff retry for network-class
# provider failures (httpx connect/read/timeout errors, HTTP 429/5xx).
# The retry runs inside the harness asyncio.timeout budget, so it can never
# extend the overall deadline; usage is counted from the successful
# response only.
MODEL_RETRY_BACKOFF_SECONDS = 2.0

_TRANSIENT_STATUS_PATTERN = re.compile(r"\bHTTP\s*[:/ ]?\s*(\d{3})\b", re.IGNORECASE)
_SECRET_PATTERN = re.compile(r"(?i)(api[_-]?key|token|authorization|bearer)\s*[:=]\s*[^\s,;]+")


def is_transient_model_error(exc: BaseException) -> bool:
    """Return ``True`` when *exc* looks like a retriable provider failure.

    Covers network-class httpx errors and HTTP 429/5xx failures, whether
    they surface as native httpx exceptions or as adapter errors carrying
    a ``status_code``/``response`` attribute or an ``HTTP <code>`` message.
    """
    if isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ReadError,
            httpx.TimeoutException,
            TimeoutError,
        ),
    ):
        return True
    for candidate in (getattr(exc, "status_code", None), getattr(getattr(exc, "response", None), "status_code", None)):
        if isinstance(candidate, int) and (candidate == 429 or 500 <= candidate <= 599):
            return True
    match = _TRANSIENT_STATUS_PATTERN.search(str(exc))
    if match:
        status = int(match.group(1))
        if status == 429 or 500 <= status <= 599:
            return True
    return False


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
    resume: "HarnessResumeInput" | None = None


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

    def __post_init__(self) -> None:
        if not self.checkpoint_id:
            raise ValueError("resume checkpoint_id must be non-empty")
        if isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError("resume attempt must be a positive integer")
        if self.start_iteration < 0:
            raise ValueError("resume start_iteration must be non-negative")
        if self.next_action is not None and not isinstance(self.next_action, Mapping):
            raise TypeError("resume next_action must be an object")


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

    def transition(self, state: RepairAttemptState, **changes: Any) -> "RepairAttempt":
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


def classify_repair_error(error: object) -> RepairErrorType:
    """Classify a tool/verifier failure without exposing raw error text."""
    text = str(error).lower()
    if any(token in text for token in ("syntaxerror", "syntax error", "parse error")):
        return RepairErrorType.SYNTAX
    if any(token in text for token in ("modulenotfound", "no module named", "dependency", "importerror")):
        return RepairErrorType.DEPENDENCY
    if any(token in text for token in ("assertionerror", "test failed", "pytest", "test_failure")):
        return RepairErrorType.TEST_FAILURE
    if any(token in text for token in ("permission denied", "access denied", "permission")):
        return RepairErrorType.PERMISSION
    if any(token in text for token in ("timed out", "timeout", "deadline")):
        return RepairErrorType.TIMEOUT
    if any(token in text for token in ("workspace conflict", "sha256", "concurrent modification", "conflict")):
        return RepairErrorType.WORKSPACE_CONFLICT
    return RepairErrorType.UNKNOWN


def _safe_repair_message(error: object) -> str:
    """Keep repair state useful while removing common credential formats."""
    value = _SECRET_PATTERN.sub(r"\1=<redacted>", str(error))
    return value[:500]


class RepairAttemptRunner:
    """Execute at most one bounded repair transaction and verify it independently.

    The planner and applier are injected so this class never invents model
    prompts or bypasses the Harness tool-permission boundary. A failed
    verification invokes rollback when supplied; no automatic second attempt
    is performed by this runner.
    """

    def __init__(
        self,
        planner: RepairPlanner,
        applier: RepairApplier,
        verifier: Verifier,
        rollback: RepairRollback | None = None,
        *,
        max_attempts: int = 1,
        token_budget: int = 1024,
        time_budget_seconds: float = 60.0,
        side_effect_budget: int = 1,
    ) -> None:
        if max_attempts < 1 or token_budget < 1 or time_budget_seconds <= 0 or side_effect_budget < 1:
            raise ValueError("repair budgets must be positive")
        self._planner = planner
        self._applier = applier
        self._verifier = verifier
        self._rollback = rollback
        self._max_attempts = max_attempts
        self._token_budget = token_budget
        self._time_budget = time_budget_seconds
        self._side_effect_budget = side_effect_budget

    async def run(
        self,
        failed_result: HarnessResult,
        error: object,
        *,
        attempt: int = 1,
    ) -> tuple[RepairAttempt, HarnessResult]:
        started = time.monotonic()
        error_type = classify_repair_error(error)
        current = RepairAttempt(
            attempt=attempt,
            error_type=error_type,
            token_budget=self._token_budget,
            time_budget_seconds=self._time_budget,
            side_effect_budget=self._side_effect_budget,
            root_cause=_safe_repair_message(error),
        ).transition(RepairAttemptState.ROOT_CAUSE_EXTRACTED)
        if current.attempt > self._max_attempts:
            return current.transition(RepairAttemptState.BUDGET_EXHAUSTED, message="repair attempt limit exhausted"), failed_result
        try:
            plan_value = self._planner(current, failed_result)
            plan = await plan_value if inspect.isawaitable(plan_value) else plan_value
            if not plan:
                return current.transition(RepairAttemptState.FAILED, message="no repair plan produced"), failed_result
            plan_text = str(plan)[:2000]
            estimated_tokens = max(1, len(plan_text) // 4)
            current = current.transition(RepairAttemptState.PLAN_READY, plan=plan_text, tokens_used=estimated_tokens)
            if current.budget_exhausted:
                return current.transition(RepairAttemptState.BUDGET_EXHAUSTED), failed_result
            current = current.transition(RepairAttemptState.APPLYING, side_effects_used=1)
            repaired = self._applier(current)
            repaired_result = await repaired if inspect.isawaitable(repaired) else repaired
            current = current.transition(RepairAttemptState.VERIFYING, elapsed_seconds=time.monotonic() - started)
            verified = self._verifier(repaired_result)
            ok = await verified if inspect.isawaitable(verified) else bool(verified)
            elapsed = time.monotonic() - started
            if elapsed > self._time_budget:
                raise TimeoutError("repair time budget exhausted")
            if ok:
                return current.transition(RepairAttemptState.SUCCEEDED, elapsed_seconds=elapsed), repaired_result
            await self._rollback_safely(current)
            return current.transition(RepairAttemptState.ROLLED_BACK, elapsed_seconds=elapsed, message="independent verifier rejected repair"), failed_result
        except TimeoutError:
            await self._rollback_safely(current)
            return current.transition(RepairAttemptState.BUDGET_EXHAUSTED, elapsed_seconds=time.monotonic() - started, message="repair time budget exhausted"), failed_result
        except Exception as exc:  # noqa: BLE001 - repair fails closed
            await self._rollback_safely(current)
            return current.transition(RepairAttemptState.FAILED, elapsed_seconds=time.monotonic() - started, message=f"repair failed: {type(exc).__name__}"), failed_result

    async def _rollback_safely(self, attempt: RepairAttempt) -> None:
        if self._rollback is None:
            return
        try:
            rollback = self._rollback(attempt)
            if inspect.isawaitable(rollback):
                await rollback
        except Exception:  # noqa: BLE001 - rollback cannot escape repair boundary
            logger.exception("repair rollback failed")

    async def run_loop(self, failed_result: HarnessResult, error: object) -> tuple[RepairAttempt, HarnessResult]:
        """Retry failed repairs up to ``max_attempts`` with rollback between tries."""
        latest_attempt: RepairAttempt | None = None
        result = failed_result
        for number in range(1, self._max_attempts + 1):
            latest_attempt, result = await self.run(result, error, attempt=number)
            if latest_attempt.state is RepairAttemptState.SUCCEEDED:
                return latest_attempt, result
            if latest_attempt.state in {RepairAttemptState.BUDGET_EXHAUSTED, RepairAttemptState.FAILED}:
                return latest_attempt, result
        assert latest_attempt is not None
        return latest_attempt.transition(RepairAttemptState.BUDGET_EXHAUSTED, message="repair attempt limit exhausted"), result


class FunctionCallingHarness:
    """Bounded model/function-call loop with an explicit per-run tool allowlist."""

    def __init__(
        self,
        model: ModelPort,
        tools: list[FunctionTool],
        *,
        max_iterations: int = 8,
        max_tool_calls: int = 32,
        max_total_tokens: int | None = None,
        max_model_cost: float | None = None,
        checkpoint_port: HarnessCheckpointPort | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        if max_total_tokens is not None and max_total_tokens < 1:
            raise ValueError("max_total_tokens must be at least 1")
        if max_model_cost is not None and (
            not math.isfinite(max_model_cost) or max_model_cost < 0
        ):
            raise ValueError("max_model_cost must be non-negative")
        tool_index = {tool.name: tool for tool in tools}
        if len(tool_index) != len(tools) or any(not name for name in tool_index):
            raise ValueError("Function tool names must be unique and non-empty")
        self._model = model
        self._tools = tool_index
        self._max_iterations = max_iterations
        self._max_tool_calls = max_tool_calls
        self._max_total_tokens = max_total_tokens
        self._max_model_cost = max_model_cost
        self._checkpoint_port = checkpoint_port

    async def execute(self, request: HarnessRequest) -> HarnessResult:
        if request.timeout <= 0:
            raise HarnessError("Harness timeout must be positive")

        started_at = time.monotonic()
        if request.on_text_delta is None and self._checkpoint_port is not None:
            publish = getattr(self._checkpoint_port, "publish_text_delta", None)
            if callable(publish):
                async def publish_delta(text: str) -> None:
                    await publish("evt-stream-" + uuid.uuid4().hex, text)
                request = replace(request, on_text_delta=publish_delta)
        recorder = _HarnessRecorder(
            self._checkpoint_port,
            request.execution,
            started_at,
            workspace_revision=request.workspace_revision,
            context_manifest_digest=request.context_manifest_digest,
        )
        resume_input = request.resume
        if resume_input is not None:
            if request.execution is None:
                raise HarnessError("resume requires an execution context")
            if resume_input.attempt != request.execution.attempt:
                raise HarnessError("resume attempt does not match execution context")
        tool_results: list[FunctionResult] = list(
            resume_input.recovered_tool_results if resume_input is not None else ()
        )
        tool_calls = 0
        iterations = resume_input.start_iteration if resume_input is not None else 0
        usage = ModelUsage()

        async def failed(
            message: str,
            *,
            budget: str | None = None,
        ) -> HarnessResult:
            if budget is not None:
                await recorder.record(
                    HarnessEventType.BUDGET_EXHAUSTED,
                    iteration=iterations,
                    tool_calls=tool_calls,
                    usage=usage,
                    tool_results=tuple(tool_results),
                    budget=budget,
                    reason=message,
                )
            await recorder.record(
                HarnessEventType.EXECUTION_FAILED,
                iteration=iterations,
                tool_calls=tool_calls,
                usage=usage,
                tool_results=tuple(tool_results),
                budget=budget,
                reason=message,
                terminal=True,
            )
            return _failed_result(
                message,
                iterations,
                tool_calls,
                _duration_ms(started_at),
                usage,
            )

        async def summarize() -> HarnessResult | None:
            """Final no-tools round after the iteration budget is exhausted.

            Mirrors the Codex top-out semantics: the executed tool results are
            handed back to the model without a tool schema so the run still
            ends with a usable summary instead of discarding completed work.
            Returns None when the summary round fails so the caller falls back
            to the original budget-exhausted failure.
            """
            nonlocal usage
            summary_request = replace(
                request,
                code=(
                    f"{request.code}\n\n"
                    "The tool iteration budget is exhausted and no further "
                    "tool calls are available. Using the tool results above, "
                    "write the final answer to the original task now."
                ),
            )
            try:
                response = await self._complete_with_retry(
                    summary_request,
                    tuple(tool_results),
                    tools_enabled=False,
                )
            except Exception:  # noqa: BLE001 - summary failure falls back to FAILED
                return None
            usage = usage.add(response.usage)
            if not response.content:
                return None
            summary_iteration = max(self._max_iterations + 1, iterations + 1)
            await recorder.record(
                HarnessEventType.ITERATION_STARTED,
                iteration=summary_iteration,
                tool_calls=tool_calls,
                usage=usage,
                tool_results=tuple(tool_results),
            )
            result = HarnessResult(
                sandbox=SandboxResult(
                    success=True,
                    stdout=response.content,
                    stderr="",
                    exit_code=0,
                    duration_ms=_duration_ms(started_at),
                    mode="function-calling",
                ),
                iterations=summary_iteration,
                tool_calls=tool_calls,
                usage=usage,
            )
            await recorder.record(
                HarnessEventType.EXECUTION_COMPLETED,
                iteration=summary_iteration,
                tool_calls=tool_calls,
                usage=usage,
                tool_results=tuple(tool_results),
                terminal=True,
            )
            return result

        try:
            async with asyncio.timeout(request.timeout):
                await recorder.record(
                    HarnessEventType.EXECUTION_STARTED,
                    iteration=iterations,
                    tool_calls=tool_calls,
                    usage=usage,
                    tool_results=tuple(tool_results),
                )
                first_iteration = iterations + 1
                for iteration in range(first_iteration, self._max_iterations + 1):
                    iterations = iteration
                    await recorder.record(
                        HarnessEventType.ITERATION_STARTED,
                        iteration=iteration,
                        tool_calls=tool_calls,
                        usage=usage,
                        tool_results=tuple(tool_results),
                    )
                    await recorder.record(
                        HarnessEventType.MODEL_STARTED,
                        iteration=iteration,
                        tool_calls=tool_calls,
                        usage=usage,
                        tool_results=tuple(tool_results),
                    )
                    if request.on_text_delta is not None:
                        response = await self._stream_with_retry(
                            request,
                            tuple(tool_results),
                            tools_enabled=bool(self._tools),
                        )
                    else:
                        response = await self._complete_with_retry(
                            request, tuple(tool_results)
                        )
                    usage = usage.add(response.usage)
                    await recorder.record(
                        HarnessEventType.MODEL_COMPLETED,
                        iteration=iteration,
                        tool_calls=tool_calls,
                        usage=usage,
                        tool_results=tuple(tool_results),
                    )
                    budget_error = self._budget_error(usage)
                    if budget_error is not None:
                        budget, message = budget_error
                        return await failed(
                            message,
                            budget=budget,
                        )
                    if not response.tool_calls and self._checkpoint_port is not None:
                        publish = getattr(self._checkpoint_port, "publish_text_delta", None)
                        if callable(publish) and response.content:
                            await publish(
                                "evt-stream-" + uuid.uuid4().hex,
                                "",
                                completed=True,
                            )
                    if not response.tool_calls:
                        if not response.content:
                            return await failed(
                                "model returned neither content nor function calls"
                            )
                        result = HarnessResult(
                            sandbox=SandboxResult(
                                success=True,
                                stdout=response.content,
                                stderr="",
                                exit_code=0,
                                duration_ms=_duration_ms(started_at),
                                mode="function-calling",
                            ),
                            iterations=iteration,
                            tool_calls=tool_calls,
                            usage=usage,
                        )
                        await recorder.record(
                            HarnessEventType.EXECUTION_COMPLETED,
                            iteration=iteration,
                            tool_calls=tool_calls,
                            usage=usage,
                            tool_results=tuple(tool_results),
                            terminal=True,
                        )
                        return result

                    for call in response.tool_calls:
                        if tool_calls >= self._max_tool_calls:
                            return await failed(
                                "Harness tool-call budget exhausted",
                                budget="tool_calls",
                            )
                        tool_calls += 1
                        await recorder.record(
                            HarnessEventType.TOOL_STARTED,
                            iteration=iteration,
                            tool_calls=tool_calls,
                            usage=usage,
                            tool_results=tuple(tool_results),
                            tool_call=call,
                        )
                        publish_tool = getattr(self._checkpoint_port, "publish_tool_event", None)
                        if callable(publish_tool):
                            await publish_tool("evt-tool-start-" + uuid.uuid4().hex, "started", call.name)
                        function_result = await self._execute_function_call(call)
                        tool_results.append(function_result)
                        if callable(publish_tool):
                            await publish_tool(
                                "evt-tool-output-" + uuid.uuid4().hex,
                                "output",
                                call.name,
                                str(function_result.content)[:4000],
                            )
                        await recorder.record(
                            HarnessEventType.TOOL_COMPLETED,
                            iteration=iteration,
                            tool_calls=tool_calls,
                            usage=usage,
                            tool_results=tuple(tool_results),
                            tool_call=call,
                            tool_success=function_result.success,
                        )
                        if callable(publish_tool):
                            await publish_tool("evt-tool-complete-" + uuid.uuid4().hex, "completed", call.name)

                # Falling out of the loop means every iteration ended with
                # executed tool calls: append one no-tools summary round so
                # completed work is not discarded (Codex top-out semantics).
                summarized = await summarize()
                if summarized is not None:
                    return summarized
        except TimeoutError:
            return await failed(
                f"Harness timed out after {request.timeout}s",
                budget="timeout",
            )
        except HarnessError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider failures become safe results
            logger.exception("harness model execution failed (%s)", type(exc).__name__)
            return await failed(f"Harness model execution failed: {type(exc).__name__}")

        return await failed(
            "Harness iteration budget exhausted before a final response",
            budget="iterations",
        )

    async def _complete_with_retry(
        self,
        request: HarnessRequest,
        tool_results: tuple[FunctionResult, ...],
        *,
        tools_enabled: bool = True,
    ) -> ModelResponse:
        """One MODEL call with a single backoff retry for transient errors.

        The retry sleeps ``MODEL_RETRY_BACKOFF_SECONDS`` and runs inside the
        caller's ``asyncio.timeout`` budget, so a pending harness deadline
        interrupts the backoff instead of being extended by it. Usage is
        only ever counted from the successful response by the caller.
        """
        model_request = self._build_model_request(
            request,
            tool_results,
            tools_enabled=tools_enabled,
            stream=False,
        )

        async def invoke() -> ModelResponse:
            method = self._model.complete
            if _uses_legacy_model_signature(method):
                kwargs: dict[str, Any] = {}
                if not tools_enabled and _accepts_keyword(method, "tools_enabled"):
                    kwargs["tools_enabled"] = False
                return await method(request, tool_results, **kwargs)  # type: ignore[call-arg]
            return await method(model_request)

        try:
            return await invoke()
        except Exception as exc:  # noqa: BLE001 - retried only when transient
            if not is_transient_model_error(exc):
                raise
            logger.warning(
                "harness: transient model error (%s: %s), retrying once in %.1fs",
                type(exc).__name__, exc, MODEL_RETRY_BACKOFF_SECONDS,
            )
            await asyncio.sleep(MODEL_RETRY_BACKOFF_SECONDS)
            return await invoke()

    async def _stream_with_retry(
        self,
        request: HarnessRequest,
        tool_results: tuple[FunctionResult, ...],
        *,
        tools_enabled: bool = False,
    ) -> ModelResponse:
        stream_method = getattr(self._model, "stream", None)
        if not callable(stream_method):
            return await self._complete_with_retry(
                request, tool_results, tools_enabled=tools_enabled
            )
        if _uses_legacy_model_signature(stream_method):
            return await stream_method(
                request, tool_results, tools_enabled=tools_enabled
            )
        model_request = self._build_model_request(
            request,
            tool_results,
            tools_enabled=tools_enabled,
            stream=True,
        )
        async def consume_once() -> ModelResponse:
            chunks: list[str] = []
            calls: list[ToolCall] = []
            usage = ModelUsage()
            delivered = False
            try:
                async for event in stream_method(model_request):
                    if not isinstance(event, ModelStreamEvent):
                        raise HarnessError(
                            "ModelPort.stream emitted a non-canonical event"
                        )
                    if event.kind == "error":
                        raise HarnessError("model stream failed") from (
                            event.error
                            if isinstance(event.error, BaseException)
                            else None
                        )
                    if event.kind == "text_delta" and event.text:
                        delivered = True
                        chunks.append(event.text)
                        if request.on_text_delta is not None:
                            callback_result = request.on_text_delta(event.text)
                            if inspect.isawaitable(callback_result):
                                await callback_result
                    if event.kind == "tool_call" and event.tool_call is not None:
                        delivered = True
                        calls.append(event.tool_call)
                    if event.kind == "completed":
                        usage = ModelUsage.from_value(event.usage)
            except Exception as exc:
                setattr(exc, "_agenthub_stream_delivered", delivered)
                raise
            return ModelResponse(
                text="".join(chunks),
                tool_calls=tuple(calls),
                usage=usage,
            )

        try:
            return await consume_once()
        except Exception as exc:
            if (
                getattr(exc, "_agenthub_stream_delivered", False)
                or not is_transient_model_error(exc)
            ):
                raise
            logger.warning(
                "harness: transient pre-stream model error (%s), retrying once",
                type(exc).__name__,
            )
            await asyncio.sleep(MODEL_RETRY_BACKOFF_SECONDS)
            return await consume_once()

    def _build_model_request(
        self,
        request: HarnessRequest,
        tool_results: tuple[ToolResult, ...],
        *,
        tools_enabled: bool,
        stream: bool,
    ) -> ModelRequest:
        messages: list[Message] = [
            Message(role="user", content=request.code, source_id="work-unit")
        ]
        for result in tool_results:
            messages.append(
                Message(
                    role="tool",
                    source_id=result.call_id,
                    content=json.dumps(
                        {
                            "callId": result.call_id,
                            "name": result.name,
                            "success": result.success,
                            "content": result.content,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            )
        metadata: dict[str, Any] = {"language": request.language}
        if request.cwd is not None:
            metadata["cwd"] = str(request.cwd)
        if request.execution is not None:
            metadata.update(
                {
                    "missionId": request.execution.mission_id,
                    "workUnitId": request.execution.work_unit_id,
                    "attempt": request.execution.attempt,
                }
            )
        tools = tuple(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.parameters),
                },
            }
            for tool in self._tools.values()
        ) if tools_enabled else ()
        return ModelRequest(
            messages=tuple(messages),
            tools=tools,
            metadata=metadata,
            stream=stream,
            tool_choice="auto" if tools_enabled else "none",
            timeout_seconds=request.timeout,
        )

    def _budget_error(self, usage: ModelUsage) -> tuple[str, str] | None:
        if (
            self._max_total_tokens is not None
            and usage.total_tokens > self._max_total_tokens
        ):
            return "total_tokens", "Harness total-token budget exhausted"
        if (
            self._max_model_cost is not None
            and usage.cost > self._max_model_cost
            and not math.isclose(
                usage.cost,
                self._max_model_cost,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        ):
            return "model_cost", "Harness model-cost budget exhausted"
        return None

    async def _execute_function_call(self, call: FunctionCall) -> FunctionResult:
        if not call.id or not call.name:
            return FunctionResult(
                call_id=call.id,
                name=call.name,
                success=False,
                content="function call must include a non-empty id and name",
            )
        if not isinstance(call.arguments, Mapping):
            return FunctionResult(
                call_id=call.id,
                name=call.name,
                success=False,
                content="function call arguments must be an object",
            )
        if not call.arguments_complete:
            return FunctionResult(
                call_id=call.id,
                name=call.name,
                success=False,
                content="function call arguments are incomplete or invalid JSON",
            )
        tool = self._tools.get(call.name)
        if tool is None:
            return FunctionResult(
                call_id=call.id,
                name=call.name,
                success=False,
                content=f"function is not permitted: {call.name}",
            )
        try:
            arguments = tool.validate_arguments(call.arguments)
        except (TypeError, ValueError) as exc:
            return FunctionResult(
                call_id=call.id,
                name=call.name,
                success=False,
                content=f"invalid function arguments: {exc}",
            )
        if not isinstance(arguments, Mapping):
            return FunctionResult(
                call_id=call.id,
                name=call.name,
                success=False,
                content="function argument validator must return an object",
            )
        try:
            content = await tool.handler(arguments)
        except Exception as exc:  # noqa: BLE001 - tool failures become model feedback
            return FunctionResult(
                call_id=call.id,
                name=call.name,
                success=False,
                content=f"function execution failed: {type(exc).__name__}",
            )
        if not isinstance(content, str):
            return FunctionResult(
                call_id=call.id,
                name=call.name,
                success=False,
                content="function handler must return text",
            )
        return FunctionResult(
            call_id=call.id,
            name=call.name,
            success=True,
            content=content,
        )


def _duration_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def _uses_legacy_model_signature(method: Any) -> bool:
    """Isolate pre-canonical test/plugin adapters at the Harness boundary."""
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False
    return "tool_results" in parameters


def _accepts_keyword(method: Any, name: str) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _failed_result(
    message: str,
    iterations: int,
    tool_calls: int,
    duration_ms: int,
    usage: ModelUsage,
) -> HarnessResult:
    return HarnessResult(
        sandbox=SandboxResult(
            success=False,
            stdout="",
            stderr=message,
            exit_code=-1,
            duration_ms=duration_ms,
            mode="function-calling",
            error=message,
        ),
        iterations=iterations,
        tool_calls=tool_calls,
        usage=usage,
    )


__all__ = [
    "RepairAttempt",
    "RepairAttemptRunner",
    "RepairAttemptState",
    "RepairErrorType",
    "classify_repair_error",
    "FunctionCall",
    "FunctionCallingHarness",
    "FunctionResult",
    "FunctionTool",
    "HarnessCheckpoint",
    "HarnessCheckpointPort",
    "HarnessError",
    "HarnessEvent",
    "HarnessEventType",
    "HarnessExecutionContext",
    "HarnessPort",
    "HarnessRequest",
    "HarnessResumeInput",
    "HarnessResult",
    "InMemoryHarnessCheckpointPort",
    "ModelPort",
    "ModelResponse",
    "ModelUsage",
    "SandboxHarness",
    "SandboxPort",
]
