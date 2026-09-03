from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
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

logger = logging.getLogger("agenthub.harness")

# G6 transient-error retry: one bounded backoff retry for network-class
# provider failures (httpx connect/read/timeout errors, HTTP 429/5xx).
# The retry runs inside the harness asyncio.timeout budget, so it can never
# extend the overall deadline; usage is counted from the successful
# response only.
MODEL_RETRY_BACKOFF_SECONDS = 2.0

_TRANSIENT_STATUS_PATTERN = re.compile(r"\bHTTP\s*[:/ ]?\s*(\d{3})\b", re.IGNORECASE)


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
class ModelUsage:
    """Provider-reported usage accumulated for one Harness execution."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.prompt_tokens, bool)
            or not isinstance(self.prompt_tokens, int)
            or self.prompt_tokens < 0
            or isinstance(self.completion_tokens, bool)
            or not isinstance(self.completion_tokens, int)
            or self.completion_tokens < 0
            or isinstance(self.cost, bool)
            or not isinstance(self.cost, (int, float))
            or not math.isfinite(self.cost)
            or self.cost < 0
        ):
            raise ValueError("Model usage values must be non-negative")

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: ModelUsage) -> ModelUsage:
        return ModelUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cost=self.cost + other.cost,
        )


@dataclass(frozen=True)
class HarnessRequest:
    """Request-scoped input for one bounded Harness execution."""

    code: str
    language: str
    timeout: float
    cwd: Path | None = None
    execution: HarnessExecutionContext | None = None
    on_text_delta: Callable[[str], Awaitable[None] | None] | None = None


@dataclass(frozen=True)
class HarnessResult:
    """Execution output plus loop metadata owned by the Harness."""

    sandbox: SandboxResult
    iterations: int = 1
    tool_calls: int = 0
    usage: ModelUsage = field(default_factory=ModelUsage)


@dataclass(frozen=True)
class FunctionCall:
    """One model-requested function invocation in a Harness loop."""

    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class FunctionResult:
    """A structured function result returned to the model on its next turn."""

    call_id: str
    name: str
    success: bool
    content: str


@dataclass(frozen=True)
class ModelResponse:
    """Model output normalized by a provider adapter before Harness handling."""

    content: str = ""
    tool_calls: tuple[FunctionCall, ...] = ()
    usage: ModelUsage = field(default_factory=ModelUsage)


class ModelPort(Protocol):
    """Model adapter used by a Harness without provider-specific state."""

    async def complete(
        self,
        request: HarnessRequest,
        tool_results: tuple[FunctionResult, ...],
        *,
        tools_enabled: bool = True,
    ) -> ModelResponse: ...

    async def stream(
        self,
        request: HarnessRequest,
        tool_results: tuple[FunctionResult, ...],
        *,
        tools_enabled: bool = True,
    ) -> ModelResponse: ...


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
        recorder = _HarnessRecorder(
            self._checkpoint_port,
            request.execution,
            started_at,
        )
        tool_results: list[FunctionResult] = []
        tool_calls = 0
        iterations = 0
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
            summary_iteration = self._max_iterations + 1
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
                for iteration in range(1, self._max_iterations + 1):
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
                            request, tuple(tool_results)
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
                        function_result = await self._execute_function_call(call)
                        tool_results.append(function_result)
                        await recorder.record(
                            HarnessEventType.TOOL_COMPLETED,
                            iteration=iteration,
                            tool_calls=tool_calls,
                            usage=usage,
                            tool_results=tuple(tool_results),
                            tool_call=call,
                            tool_success=function_result.success,
                        )

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
        # Preserve the historical call shapes: the loop call passes no
        # keyword (ModelPort defaults tools_enabled), the summary call
        # disables tools explicitly. Adapter stubs may implement either
        # signature only.
        kwargs: dict[str, Any] = {} if tools_enabled else {"tools_enabled": False}

        def invoke() -> Any:
            return self._model.complete(request, tool_results, **kwargs)

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
    ) -> ModelResponse:
        stream = getattr(self._model, "stream", None)
        if not callable(stream):
            return await self._complete_with_retry(request, tool_results)
        return await stream(request, tool_results, tools_enabled=False)

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
    "HarnessResult",
    "InMemoryHarnessCheckpointPort",
    "ModelPort",
    "ModelResponse",
    "ModelUsage",
    "SandboxHarness",
    "SandboxPort",
]
