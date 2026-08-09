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
    HarnessError,
    HarnessRequest,
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

    async def complete(
        self,
        request: HarnessRequest,
        tool_results: tuple[FunctionResult, ...],
    ) -> ModelResponse:
        self.calls.append((request, tool_results))
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
        ).execute(HarnessRequest(code="x", language="text", timeout=1))

        self.assertFalse(result.sandbox.success)
        self.assertEqual(result.sandbox.error, "Harness total-token budget exhausted")
        self.assertFalse(executed)
        self.assertEqual(result.usage.total_tokens, 5)

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
