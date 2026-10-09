"""Bounded resumable loop, separate from model and tool adapters."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import replace
from typing import Any

from app.services.harness_checkpoint import HarnessError, _HarnessRecorder
from app.services.harness_checkpoint import HarnessEventType as Phase
from app.services.harness_types import HarnessRequest, HarnessResult
from app.services.model_contract import ModelResponse, ModelUsage
from app.services.tools.sandbox_executor import SandboxResult

logger = logging.getLogger("agenthub.harness")


def _initial_timing(request: HarnessRequest) -> tuple[float, float]:
    resume = request.resume
    elapsed = resume.elapsed_seconds if resume else 0
    deadline = resume.deadline_epoch if resume and resume.deadline_epoch is not None else time.time() + request.timeout - elapsed
    elapsed = max(elapsed, request.timeout - max(0.0, deadline - time.time()))
    return time.monotonic() - elapsed, deadline


class HarnessLoop:
    """One execution's mutable state; durable snapshots contain every budget counter."""

    def __init__(self, harness: Any, request: HarnessRequest) -> None:
        self.harness = harness
        self.request = request
        resume = request.resume
        if resume is not None and (
            request.execution is None or resume.attempt != request.execution.attempt
        ):
            raise HarnessError("resume attempt does not match execution context")
        self.started_at, self.deadline_epoch = _initial_timing(request)
        self.results = list(resume.recovered_tool_results if resume else ())
        self.calls = resume.tool_calls if resume else 0
        self.iteration = resume.start_iteration if resume else 0
        self.next_iteration = resume.next_iteration if resume and resume.next_iteration is not None else self.iteration + 1
        self.usage = resume.usage if resume else ModelUsage()
        self.pending = list(resume.pending_tool_calls if resume else ())
        self.response_content = resume.response_content if resume else None
        self.reserved = resume.reserved_call_id if resume else None
        self.recorder = _HarnessRecorder(
            harness._checkpoint_port, request.execution, self.started_at,
            request.workspace_revision, request.context_manifest_digest,
            base_sequence=resume.checkpoint_sequence if resume else 0,
        )

    async def record(self, phase: Phase, **extra: Any) -> None:
        await self.recorder.record(
            phase, iteration=self.iteration, tool_calls=self.calls, usage=self.usage,
            tool_results=tuple(self.results), pending_tool_calls=tuple(self.pending),
            response_content=self.response_content, reserved_call_id=self.reserved, **extra,
            next_iteration=self.next_iteration,
            deadline_epoch=self.deadline_epoch,
        )

    def result(self, success: bool, text: str) -> HarnessResult:
        return HarnessResult(
            sandbox=SandboxResult(success=success, stdout=text if success else "",
                                  stderr="" if success else text, exit_code=0 if success else 1,
                                  duration_ms=int((time.monotonic() - self.started_at) * 1000),
                                  error="" if success else text,
                                  mode="function-calling"),
            iterations=self.iteration, tool_calls=self.calls, usage=self.usage,
        )

    async def failed(self, message: str, budget: str | None = None) -> HarnessResult:
        if budget:
            await self.record(Phase.BUDGET_EXHAUSTED, budget=budget, reason=message)
        await self.record(Phase.EXECUTION_FAILED, budget=budget, reason=message, terminal=True)
        return self.result(False, message)

    async def finish(self) -> HarnessResult:
        if not self.response_content:
            return await self.failed("model returned neither content nor function calls")
        await self.record(Phase.EXECUTION_COMPLETED, terminal=True)
        return self.result(True, self.response_content)

    async def budget_failure(self) -> HarnessResult | None:
        error = self.harness._budget_error(self.usage)
        return await self.failed(error[1], error[0]) if error else None

    async def fetch_response(self) -> None:
        self.next_iteration = self.iteration
        self.response_content = None
        await self.record(Phase.ITERATION_STARTED)
        await self.record(Phase.MODEL_STARTED)
        if self.request.on_text_delta is not None:
            response = await self.harness._stream_with_retry(
                self.request, tuple(self.results), tools_enabled=bool(self.harness._tools))
        else:
            response = await self.harness._complete_with_retry(self.request, tuple(self.results))
        self.usage = self.usage.add(response.usage)
        self.response_content = response.content
        self.pending = list(response.tool_calls)
        self.next_iteration = self.iteration + 1
        await self.record(Phase.MODEL_COMPLETED)

    async def publish_tool(self, kind: str, name: str, text: str = "") -> None:
        publish = getattr(self.harness._checkpoint_port, "publish_tool_event", None)
        if callable(publish):
            await publish("evt-tool-" + kind + "-" + uuid.uuid4().hex, kind, name, text)

    async def execute_pending(self) -> HarnessResult | None:
        while self.pending:
            call = self.pending[0]
            if self.reserved != call.id:
                if self.calls >= self.harness._max_tool_calls:
                    return await self.failed("Harness tool-call budget exhausted", "tool_calls")
                self.calls += 1
                self.reserved = call.id
            await self.record(Phase.TOOL_STARTED, tool_call=call)
            await self.publish_tool("started", call.name)
            result = await self.harness._execute_function_call(call, self.request.execution)
            if self.harness._feedback_policy is not None:
                from app.services.tool_feedback import apply_tool_feedback
                result = apply_tool_feedback(result, self.results, self.harness._feedback_policy)
            self.results.append(result)
            self.pending.pop(0)
            self.reserved = None
            await self.publish_tool("output", call.name, str(result.content)[:4000])
            await self.record(Phase.TOOL_COMPLETED, tool_call=call, tool_success=result.success)
            await self.publish_tool("completed", call.name)
        self.response_content = None
        return None

    async def summarize(self) -> HarnessResult | None:
        request = replace(self.request, code=(f"{self.request.code}\n\n"
            "The tool iteration budget is exhausted and no further tool calls are available. "
            "Using the tool results above, write the final answer to the original task now."))
        try:
            self.iteration = max(self.harness._max_iterations + 1, self.next_iteration)
            self.next_iteration = self.iteration
            await self.record(Phase.ITERATION_STARTED)
            await self.record(Phase.MODEL_STARTED)
            response: ModelResponse = await self.harness._complete_with_retry(
                request, tuple(self.results), tools_enabled=False)
        except HarnessError:
            raise
        except Exception:
            return None
        self.usage = self.usage.add(response.usage)
        self.response_content = response.content
        await self.record(Phase.MODEL_COMPLETED)
        if response.tool_calls:
            return await self.failed("summary model returned forbidden tool calls")
        error = await self.budget_failure()
        if error is not None:
            return error
        return await self.finish() if response.content else None

    async def run_iterations(self) -> HarnessResult:
        # A completed model response is consumed once before any new model call.
        resume = self.request.resume
        if self.pending:
            error = await self.budget_failure()
            if error is not None:
                return error
            error = await self.execute_pending()
            if error is not None:
                return error
        elif self.response_content is not None:
            error = await self.budget_failure()
            return error if error is not None else await self.finish()
        first = self.next_iteration
        if resume and resume.phase == Phase.ITERATION_STARTED.value:
            first = max(1, self.iteration)
        for self.iteration in range(first, self.harness._max_iterations + 1):
            await self.fetch_response()
            error = await self.budget_failure()
            if error is not None:
                return error
            if not self.pending:
                return await self.finish()
            error = await self.execute_pending()
            if error is not None:
                return error
        summary = await self.summarize()
        return summary if summary is not None else await self.failed(
            "Harness iteration budget exhausted before a final response", "iterations")

    async def run(self) -> HarnessResult:
        if self.request.timeout <= 0:
            raise HarnessError("Harness timeout must be positive")
        if self.request.resume and self.request.resume.phase == Phase.EXECUTION_COMPLETED.value:
            return self.result(True, self.response_content or "")
        remaining = min(self.request.timeout - (time.monotonic() - self.started_at),
                        self.deadline_epoch - time.time())
        if remaining <= 0:
            return await self.failed(f"Harness timed out after {self.request.timeout}s", "timeout")
        try:
            async with asyncio.timeout(max(0, remaining)):
                await self.record(Phase.EXECUTION_STARTED)
                return await self.run_iterations()
        except TimeoutError:
            return await self.failed(f"Harness timed out after {self.request.timeout}s", "timeout")
        except HarnessError:
            raise
        except Exception as exc:
            logger.exception("harness model execution failed (%s)", type(exc).__name__)
            return await self.failed(f"Harness model execution failed: {type(exc).__name__}")
