from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.services.tools.sandbox_executor import SandboxResult


class HarnessError(RuntimeError):
    """Raised when a Harness cannot execute a bounded WorkUnit request."""


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

    async def execute(self, request: HarnessRequest) -> HarnessResult:
        if request.timeout <= 0:
            raise HarnessError("Harness timeout must be positive")

        started_at = time.monotonic()
        tool_results: list[FunctionResult] = []
        tool_calls = 0
        iterations = 0
        usage = ModelUsage()
        try:
            async with asyncio.timeout(request.timeout):
                for iteration in range(1, self._max_iterations + 1):
                    iterations = iteration
                    response = await self._model.complete(request, tuple(tool_results))
                    usage = usage.add(response.usage)
                    budget_error = self._budget_error(usage)
                    if budget_error is not None:
                        return _failed_result(
                            budget_error,
                            iteration,
                            tool_calls,
                            _duration_ms(started_at),
                            usage,
                        )
                    if not response.tool_calls:
                        if not response.content:
                            return _failed_result(
                                "model returned neither content nor function calls",
                                iteration,
                                tool_calls,
                                _duration_ms(started_at),
                                usage,
                            )
                        return HarnessResult(
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

                    for call in response.tool_calls:
                        tool_calls += 1
                        if tool_calls > self._max_tool_calls:
                            return _failed_result(
                                "Harness tool-call budget exhausted",
                                iteration,
                                tool_calls - 1,
                                _duration_ms(started_at),
                                usage,
                            )
                        tool_results.append(await self._execute_function_call(call))
        except TimeoutError:
            return _failed_result(
                f"Harness timed out after {request.timeout}s",
                iterations,
                tool_calls,
                _duration_ms(started_at),
                usage,
            )

        return _failed_result(
            "Harness iteration budget exhausted before a final response",
            iterations,
            tool_calls,
            _duration_ms(started_at),
            usage,
        )

    def _budget_error(self, usage: ModelUsage) -> str | None:
        if (
            self._max_total_tokens is not None
            and usage.total_tokens > self._max_total_tokens
        ):
            return "Harness total-token budget exhausted"
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
            return "Harness model-cost budget exhausted"
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
    "HarnessError",
    "HarnessPort",
    "HarnessRequest",
    "HarnessResult",
    "ModelPort",
    "ModelResponse",
    "ModelUsage",
    "SandboxHarness",
    "SandboxPort",
]
