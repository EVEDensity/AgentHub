from __future__ import annotations

import asyncio
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.services.harness_service import (
    FunctionCall,
    FunctionCallingHarness,
    FunctionResult,
    FunctionTool,
    HarnessCheckpoint,
    HarnessCheckpointPort,
    HarnessError,
    HarnessEvent,
    HarnessEventType,
    HarnessExecutionContext,
    HarnessRequest,
    InMemoryHarnessCheckpointPort,
    ModelResponse,
    ModelUsage,
    SandboxHarness,
)
from app.services.tools.sandbox_executor import SandboxResult


class FakeSandbox:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(self, code: str, **kwargs: object) -> SandboxResult:
        self.calls.append({"code": code, **kwargs})
        return SandboxResult(
            success=True,
            stdout="ok",
            stderr="",
            exit_code=0,
            duration_ms=1,
            mode="fake",
        )


class ScriptedModel:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[HarnessRequest, tuple[FunctionResult, ...]]] = []
        self.tools_enabled: list[bool] = []

    async def complete(
        self,
        request: HarnessRequest,
        tool_results: tuple[FunctionResult, ...],
        *,
        tools_enabled: bool = True,
    ) -> ModelResponse:
        self.calls.append((request, tool_results))
        self.tools_enabled.append(tools_enabled)
        return self.responses.pop(0)


def _validate_count(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    count = arguments.get("count")
    if not isinstance(count, int) or count < 0:
        raise ValueError("count must be a non-negative integer")
    return {"count": count}


class HarnessServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sandbox_harness_forwards_request_and_adds_loop_metadata(self) -> None:
        sandbox = FakeSandbox()
        result = await SandboxHarness(sandbox).execute(
            HarnessRequest(
                code="print('ok')",
                language="python",
                timeout=5,
                cwd=Path("workspace"),
            )
        )

        self.assertTrue(result.sandbox.success)
        self.assertEqual(result.iterations, 1)
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(
            sandbox.calls,
            [{"code": "print('ok')", "language": "python", "timeout": 5, "cwd": "workspace"}],
        )

    async def test_sandbox_harness_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(HarnessError, "timeout must be positive"):
            await SandboxHarness(FakeSandbox()).execute(
                HarnessRequest(code="", language="python", timeout=0)
            )

    async def test_function_calling_harness_returns_final_model_output(self) -> None:
        observed_arguments: list[dict[str, Any]] = []

        async def double(arguments: Mapping[str, Any]) -> str:
            observed_arguments.append(dict(arguments))
            return str(arguments["count"] * 2)

        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        FunctionCall(
                            id="call-1",
                            name="double",
                            arguments={"count": 3},
                        ),
                    ),
                    usage=ModelUsage(prompt_tokens=4, completion_tokens=2, cost=0.1),
                ),
                ModelResponse(
                    content="The result is 6.",
                    usage=ModelUsage(prompt_tokens=3, completion_tokens=4, cost=0.2),
                ),
            ]
        )
        harness = FunctionCallingHarness(
            model,
            [
                FunctionTool(
                    name="double",
                    handler=double,
                    validate_arguments=_validate_count,
                )
            ],
        )

        result = await harness.execute(
            HarnessRequest(code="Double 3", language="text", timeout=5)
        )

        self.assertTrue(result.sandbox.success)
        self.assertEqual(result.sandbox.stdout, "The result is 6.")
        self.assertEqual(result.iterations, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.usage.total_tokens, 13)
        self.assertAlmostEqual(result.usage.cost, 0.3)
        self.assertEqual(observed_arguments, [{"count": 3}])
        self.assertEqual(model.calls[1][1][0].content, "6")

    async def test_function_calling_harness_records_correlated_execution_journal(self) -> None:
        async def double(arguments: Mapping[str, Any]) -> str:
            return str(arguments["count"] * 2)

        execution = HarnessExecutionContext(
            mission_id="mis-1",
            work_unit_id="wu-1",
            attempt=2,
        )
        checkpoint_port = InMemoryHarnessCheckpointPort()
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        FunctionCall(id="call-1", name="double", arguments={"count": 3}),
                    ),
                    usage=ModelUsage(prompt_tokens=4, completion_tokens=2),
                ),
                ModelResponse(
                    content="done",
                    usage=ModelUsage(prompt_tokens=3, completion_tokens=1),
                ),
            ]
        )
        result = await FunctionCallingHarness(
            model,
            [
                FunctionTool(
                    name="double",
                    handler=double,
                    validate_arguments=_validate_count,
                )
            ],
            checkpoint_port=checkpoint_port,
        ).execute(
            HarnessRequest(
                code="Double 3",
                language="text",
                timeout=1,
                execution=execution,
            )
        )

        self.assertTrue(result.sandbox.success)
        self.assertEqual(
            [event.event_type for event in checkpoint_port.events],
            [
                HarnessEventType.EXECUTION_STARTED,
                HarnessEventType.ITERATION_STARTED,
                HarnessEventType.MODEL_STARTED,
                HarnessEventType.MODEL_COMPLETED,
                HarnessEventType.TOOL_STARTED,
                HarnessEventType.TOOL_COMPLETED,
                HarnessEventType.ITERATION_STARTED,
                HarnessEventType.MODEL_STARTED,
                HarnessEventType.MODEL_COMPLETED,
                HarnessEventType.EXECUTION_COMPLETED,
            ],
        )
        self.assertEqual(
            [event.sequence for event in checkpoint_port.events],
            list(range(1, 11)),
        )
        self.assertTrue(all(event.execution == execution for event in checkpoint_port.events))
        tool_event = checkpoint_port.events[5]
        self.assertEqual(tool_event.tool_call_id, "call-1")
        self.assertEqual(tool_event.tool_name, "double")
        self.assertTrue(tool_event.tool_success)
        self.assertEqual(checkpoint_port.checkpoints[5].tool_results[0].content, "6")
        assert checkpoint_port.latest is not None
        self.assertTrue(checkpoint_port.latest.terminal)
        self.assertEqual(checkpoint_port.latest.iteration, 2)
        self.assertEqual(checkpoint_port.latest.usage.total_tokens, 10)

    async def test_function_calling_harness_rejects_unpermitted_calls_as_feedback(self) -> None:
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        FunctionCall(id="call-1", name="delete_everything", arguments={}),
                    )
                ),
                ModelResponse(content="I cannot perform that operation."),
            ]
        )
        harness = FunctionCallingHarness(model, [])

        result = await harness.execute(
            HarnessRequest(code="Delete the workspace", language="text", timeout=5)
        )

        self.assertTrue(result.sandbox.success)
        self.assertFalse(model.calls[1][1][0].success)
        self.assertIn("not permitted", model.calls[1][1][0].content)

    async def test_function_calling_harness_stops_when_iteration_budget_is_exhausted(self) -> None:
        async def no_op(arguments: Mapping[str, Any]) -> str:
            return str(arguments["count"])

        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(FunctionCall(id="call-1", name="noop", arguments={"count": 1}),)
                ),
                ModelResponse(
                    tool_calls=(FunctionCall(id="call-2", name="noop", arguments={"count": 2}),)
                ),
            ]
        )
        harness = FunctionCallingHarness(
            model,
            [FunctionTool(name="noop", handler=no_op, validate_arguments=_validate_count)],
            max_iterations=2,
        )

        result = await harness.execute(
            HarnessRequest(code="Keep calling tools", language="text", timeout=5)
        )

        self.assertFalse(result.sandbox.success)
        self.assertIn("iteration budget exhausted", result.sandbox.error)
        self.assertEqual(result.tool_calls, 2)

    async def test_function_calling_harness_appends_no_tools_summary_round_when_iterations_exhausted(self) -> None:
        async def no_op(arguments: Mapping[str, Any]) -> str:
            return str(arguments["count"])

        checkpoint_port = InMemoryHarnessCheckpointPort()
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        FunctionCall(id="call-1", name="noop", arguments={"count": 1}),
                    ),
                    usage=ModelUsage(prompt_tokens=3, completion_tokens=2, cost=0.1),
                ),
                ModelResponse(
                    tool_calls=(
                        FunctionCall(id="call-2", name="noop", arguments={"count": 2}),
                    ),
                    usage=ModelUsage(prompt_tokens=3, completion_tokens=2, cost=0.1),
                ),
                ModelResponse(
                    content="Summary of the completed work.",
                    usage=ModelUsage(prompt_tokens=5, completion_tokens=4, cost=0.2),
                ),
            ]
        )
        harness = FunctionCallingHarness(
            model,
            [FunctionTool(name="noop", handler=no_op, validate_arguments=_validate_count)],
            max_iterations=2,
            checkpoint_port=checkpoint_port,
        )

        result = await harness.execute(
            HarnessRequest(code="Keep calling tools", language="text", timeout=5)
        )

        self.assertTrue(result.sandbox.success)
        self.assertEqual(result.sandbox.stdout, "Summary of the completed work.")
        self.assertEqual(result.iterations, 3)
        self.assertEqual(result.tool_calls, 2)
        self.assertEqual(result.usage.total_tokens, 19)
        self.assertAlmostEqual(result.usage.cost, 0.4)
        # The summary round is a pure-text call without the tool schema and
        # receives the executed tool results.
        self.assertEqual(model.tools_enabled, [True, True, False])
        summary_request, summary_results = model.calls[2]
        self.assertIn("tool iteration budget is exhausted", summary_request.code)
        self.assertIn("final answer", summary_request.code)
        self.assertEqual(len(summary_results), 2)
        self.assertEqual(
            [event.event_type for event in checkpoint_port.events],
            [
                HarnessEventType.EXECUTION_STARTED,
                HarnessEventType.ITERATION_STARTED,
                HarnessEventType.MODEL_STARTED,
                HarnessEventType.MODEL_COMPLETED,
                HarnessEventType.TOOL_STARTED,
                HarnessEventType.TOOL_COMPLETED,
                HarnessEventType.ITERATION_STARTED,
                HarnessEventType.MODEL_STARTED,
                HarnessEventType.MODEL_COMPLETED,
                HarnessEventType.TOOL_STARTED,
                HarnessEventType.TOOL_COMPLETED,
                # The summary round reuses the iteration marker one past the
                # budget so durable phase constraints stay valid.
                HarnessEventType.ITERATION_STARTED,
                HarnessEventType.EXECUTION_COMPLETED,
            ],
        )
        self.assertEqual(checkpoint_port.events[-2].iteration, 3)
        assert checkpoint_port.latest is not None
        self.assertTrue(checkpoint_port.latest.terminal)
        self.assertEqual(checkpoint_port.latest.iteration, 3)

    async def test_function_calling_harness_falls_back_to_failed_when_summary_round_fails(self) -> None:
        async def no_op(arguments: Mapping[str, Any]) -> str:
            return str(arguments["count"])

        class FailingSummaryModel:
            def __init__(self) -> None:
                self.calls = 0
                self.tools_enabled: list[bool] = []

            async def complete(
                self,
                request: HarnessRequest,
                tool_results: tuple[FunctionResult, ...],
                *,
                tools_enabled: bool = True,
            ) -> ModelResponse:
                del request, tool_results
                self.calls += 1
                self.tools_enabled.append(tools_enabled)
                if self.calls > 2:
                    raise RuntimeError("summary provider unavailable")
                return ModelResponse(
                    tool_calls=(
                        FunctionCall(
                            id=f"call-{self.calls}",
                            name="noop",
                            arguments={"count": self.calls},
                        ),
                    )
                )

        checkpoint_port = InMemoryHarnessCheckpointPort()
        model = FailingSummaryModel()
        harness = FunctionCallingHarness(
            model,
            [FunctionTool(name="noop", handler=no_op, validate_arguments=_validate_count)],
            max_iterations=2,
            checkpoint_port=checkpoint_port,
        )

        result = await harness.execute(
            HarnessRequest(code="Keep calling tools", language="text", timeout=5)
        )

        self.assertFalse(result.sandbox.success)
        self.assertIn("iteration budget exhausted", result.sandbox.error)
        self.assertEqual(result.tool_calls, 2)
        self.assertEqual(model.tools_enabled, [True, True, False])
        self.assertEqual(
            [event.event_type for event in checkpoint_port.events[-2:]],
            [
                HarnessEventType.BUDGET_EXHAUSTED,
                HarnessEventType.EXECUTION_FAILED,
            ],
        )
        self.assertEqual(checkpoint_port.events[-1].budget, "iterations")

    async def test_function_calling_harness_does_not_summarize_on_early_completion(self) -> None:
        async def no_op(arguments: Mapping[str, Any]) -> str:
            return str(arguments["count"])

        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        FunctionCall(id="call-1", name="noop", arguments={"count": 1}),
                    )
                ),
                ModelResponse(content="done"),
            ]
        )
        harness = FunctionCallingHarness(
            model,
            [FunctionTool(name="noop", handler=no_op, validate_arguments=_validate_count)],
            max_iterations=8,
        )

        result = await harness.execute(
            HarnessRequest(code="Finish quickly", language="text", timeout=5)
        )

        self.assertTrue(result.sandbox.success)
        self.assertEqual(result.sandbox.stdout, "done")
        self.assertEqual(result.iterations, 2)
        self.assertEqual(len(model.calls), 2)
        self.assertTrue(all(model.tools_enabled))

    async def test_function_calling_harness_does_not_summarize_on_tool_call_budget(self) -> None:
        async def no_op(arguments: Mapping[str, Any]) -> str:
            return str(arguments["count"])

        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        FunctionCall(id="call-1", name="noop", arguments={"count": 1}),
                        FunctionCall(id="call-2", name="noop", arguments={"count": 2}),
                    )
                )
            ]
        )
        harness = FunctionCallingHarness(
            model,
            [FunctionTool(name="noop", handler=no_op, validate_arguments=_validate_count)],
            max_tool_calls=1,
        )

        result = await harness.execute(
            HarnessRequest(code="Call twice", language="text", timeout=5)
        )

        self.assertFalse(result.sandbox.success)
        self.assertIn("tool-call budget exhausted", result.sandbox.error)
        # Tool abuse tops out immediately: no summary round is attempted.
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.tools_enabled, [True])

    async def test_function_calling_harness_returns_validation_and_execution_failures_to_model(self) -> None:
        handler_called = False

        async def explode(arguments: Mapping[str, Any]) -> str:
            del arguments
            raise RuntimeError("secret internal detail")

        async def should_not_run(arguments: Mapping[str, Any]) -> str:
            nonlocal handler_called
            handler_called = True
            return str(arguments["count"])

        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        FunctionCall(id="call-1", name="validate", arguments={"count": -1}),
                        FunctionCall(id="call-2", name="explode", arguments={"count": 1}),
                    )
                ),
                ModelResponse(content="The tools could not complete the request."),
            ]
        )
        harness = FunctionCallingHarness(
            model,
            [
                FunctionTool(
                    name="validate",
                    handler=should_not_run,
                    validate_arguments=_validate_count,
                ),
                FunctionTool(
                    name="explode",
                    handler=explode,
                    validate_arguments=_validate_count,
                ),
            ],
        )

        result = await harness.execute(
            HarnessRequest(code="Use both tools", language="text", timeout=5)
        )

        self.assertTrue(result.sandbox.success)
        self.assertFalse(handler_called)
        feedback = model.calls[1][1]
        self.assertEqual(len(feedback), 2)
        self.assertIn("invalid function arguments", feedback[0].content)
        self.assertIn("RuntimeError", feedback[1].content)
        self.assertNotIn("secret internal detail", feedback[1].content)

    async def test_function_calling_harness_stops_when_tool_call_budget_is_exhausted(self) -> None:
        executed: list[int] = []

        async def no_op(arguments: Mapping[str, Any]) -> str:
            executed.append(arguments["count"])
            return "ok"

        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        FunctionCall(id="call-1", name="noop", arguments={"count": 1}),
                        FunctionCall(id="call-2", name="noop", arguments={"count": 2}),
                    )
                )
            ]
        )
        harness = FunctionCallingHarness(
            model,
            [FunctionTool(name="noop", handler=no_op, validate_arguments=_validate_count)],
            max_tool_calls=1,
        )

        result = await harness.execute(
            HarnessRequest(code="Call twice", language="text", timeout=5)
        )

        self.assertFalse(result.sandbox.success)
        self.assertIn("tool-call budget exhausted", result.sandbox.error)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(executed, [1])

    async def test_function_calling_harness_stops_at_total_timeout(self) -> None:
        class BlockingModel:
            async def complete(
                self,
                request: HarnessRequest,
                tool_results: tuple[FunctionResult, ...],
            ) -> ModelResponse:
                del request, tool_results
                await asyncio.sleep(1)
                return ModelResponse(content="too late")

        result = await FunctionCallingHarness(BlockingModel(), []).execute(
            HarnessRequest(code="Wait", language="text", timeout=0.01)
        )

        self.assertFalse(result.sandbox.success)
        self.assertIn("timed out", result.sandbox.error)
        self.assertEqual(result.iterations, 1)

    async def test_function_calling_harness_enforces_total_token_budget_before_tools(self) -> None:
        executed = False

        async def tool(arguments: Mapping[str, Any]) -> str:
            nonlocal executed
            del arguments
            executed = True
            return "should not run"

        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(FunctionCall(id="call-1", name="tool", arguments={}),),
                    usage=ModelUsage(prompt_tokens=3, completion_tokens=2),
                )
            ]
        )
        checkpoint_port = InMemoryHarnessCheckpointPort()
        result = await FunctionCallingHarness(
            model,
            [
                FunctionTool(
                    name="tool",
                    handler=tool,
                    validate_arguments=lambda args: args,
                )
            ],
            max_total_tokens=4,
            checkpoint_port=checkpoint_port,
        ).execute(HarnessRequest(code="x", language="text", timeout=1))

        self.assertFalse(result.sandbox.success)
        self.assertEqual(result.sandbox.error, "Harness total-token budget exhausted")
        self.assertFalse(executed)
        self.assertEqual(result.usage.total_tokens, 5)
        self.assertEqual(
            [event.event_type for event in checkpoint_port.events[-2:]],
            [
                HarnessEventType.BUDGET_EXHAUSTED,
                HarnessEventType.EXECUTION_FAILED,
            ],
        )
        self.assertEqual(checkpoint_port.events[-1].budget, "total_tokens")
        assert checkpoint_port.latest is not None
        self.assertTrue(checkpoint_port.latest.terminal)
        self.assertEqual(
            checkpoint_port.latest.failure_reason,
            "Harness total-token budget exhausted",
        )

    async def test_function_calling_harness_enforces_model_cost_budget(self) -> None:
        model = ScriptedModel(
            [ModelResponse(content="done", usage=ModelUsage(cost=0.11))]
        )
        result = await FunctionCallingHarness(
            model,
            [],
            max_model_cost=0.1,
        ).execute(HarnessRequest(code="x", language="text", timeout=1))

        self.assertFalse(result.sandbox.success)
        self.assertEqual(result.sandbox.error, "Harness model-cost budget exhausted")
        self.assertEqual(result.usage.cost, 0.11)

    async def test_function_calling_harness_allows_exact_accumulated_cost_budget(self) -> None:
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        FunctionCall(id="call-1", name="missing", arguments={}),
                    ),
                    usage=ModelUsage(cost=0.1),
                ),
                ModelResponse(content="done", usage=ModelUsage(cost=0.2)),
            ]
        )
        result = await FunctionCallingHarness(
            model,
            [],
            max_model_cost=0.3,
        ).execute(HarnessRequest(code="x", language="text", timeout=1))

        self.assertTrue(result.sandbox.success)
        self.assertAlmostEqual(result.usage.cost, 0.3)

    def test_model_usage_rejects_negative_values(self) -> None:
        with self.assertRaises(ValueError):
            ModelUsage(prompt_tokens=-1)
        with self.assertRaises(ValueError):
            ModelUsage(completion_tokens=-1)
        with self.assertRaises(ValueError):
            ModelUsage(cost=-0.1)
        with self.assertRaises(ValueError):
            ModelUsage(prompt_tokens=1.5)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ModelUsage(cost=float("nan"))
        with self.assertRaises(ValueError):
            FunctionCallingHarness(ScriptedModel([]), [], max_model_cost=float("nan"))

    async def test_in_memory_checkpoint_port_cannot_be_reused_for_another_run(self) -> None:
        checkpoint_port = InMemoryHarnessCheckpointPort()
        harness = FunctionCallingHarness(
            ScriptedModel(
                [ModelResponse(content="first"), ModelResponse(content="second")]
            ),
            [],
            checkpoint_port=checkpoint_port,
        )

        first = await harness.execute(HarnessRequest(code="x", language="text", timeout=1))
        self.assertTrue(first.sandbox.success)
        with self.assertRaisesRegex(HarnessError, "request-scoped"):
            await harness.execute(HarnessRequest(code="y", language="text", timeout=1))

    async def test_checkpoint_adapter_failure_stops_before_model_execution(self) -> None:
        class FailingCheckpointPort(HarnessCheckpointPort):
            async def record(
                self,
                checkpoint: HarnessCheckpoint,
                event: HarnessEvent,
            ) -> None:
                del checkpoint, event
                raise RuntimeError("unavailable")

        model = ScriptedModel([ModelResponse(content="must not run")])
        harness = FunctionCallingHarness(
            model,
            [],
            checkpoint_port=FailingCheckpointPort(),
        )

        with self.assertRaisesRegex(HarnessError, "checkpoint recording failed"):
            await harness.execute(HarnessRequest(code="x", language="text", timeout=1))
        self.assertEqual(model.calls, [])

    async def test_model_failure_records_safe_terminal_event(self) -> None:
        class FailingModel:
            async def complete(
                self,
                request: HarnessRequest,
                tool_results: tuple[FunctionResult, ...],
            ) -> ModelResponse:
                del request, tool_results
                raise RuntimeError("provider secret")

        checkpoint_port = InMemoryHarnessCheckpointPort()
        result = await FunctionCallingHarness(
            FailingModel(),
            [],
            checkpoint_port=checkpoint_port,
        ).execute(HarnessRequest(code="x", language="text", timeout=1))

        self.assertFalse(result.sandbox.success)
        self.assertEqual(
            result.sandbox.error,
            "Harness model execution failed: RuntimeError",
        )
        self.assertNotIn("provider secret", result.sandbox.error)
        self.assertEqual(
            checkpoint_port.events[-1].event_type,
            HarnessEventType.EXECUTION_FAILED,
        )

    def test_execution_context_requires_stable_attempt_identity(self) -> None:
        with self.assertRaises(ValueError):
            HarnessExecutionContext(mission_id="", work_unit_id="wu-1", attempt=1)
        with self.assertRaises(ValueError):
            HarnessExecutionContext(mission_id="mis-1", work_unit_id="wu-1", attempt=0)
