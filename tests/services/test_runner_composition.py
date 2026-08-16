from __future__ import annotations

import copy
import unittest
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from app.services.capability_tools import CapabilityToolBinding
from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.harness_service import (
    FunctionCall,
    FunctionResult,
    FunctionTool,
    HarnessRequest,
    ModelResponse,
    ModelUsage,
)
from app.services.runner_composition import (
    A2AInboundHarnessFactory,
    build_a2a_inbound_runner,
)
from app.services.runner_service import ClaimedWorkResolutionError
from tests.services.test_runner_service import (
    FakePublisher,
    inbound_claim_payload,
    inbound_execution_context,
)


def valid_execution_context() -> dict[str, Any]:
    context = copy.deepcopy(inbound_execution_context())
    context["workUnit"]["dependencies"] = []
    del context["workUnit"]["inputRefs"][0]["contentAddress"]
    return context


def read_binding() -> CapabilityToolBinding:
    def validate(
        arguments: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        path = arguments.get("path")
        if not isinstance(path, str) or path != scope.get("path"):
            raise ValueError("path is outside the Contract scope")
        return {"path": path}

    async def handle(
        arguments: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> str:
        return f"read:{arguments['path']}:{scope['path']}"

    return CapabilityToolBinding(
        capability="repository.read",
        function_name="read_file",
        description="Read one Contract-scoped file",
        parameters={"type": "object", "required": ["path"]},
        validate_arguments=validate,
        handler=handle,
    )


class RecordingBindingFactory:
    def __init__(
        self,
        bindings: Sequence[CapabilityToolBinding] = (),
        error: Exception | None = None,
    ) -> None:
        self.bindings = list(bindings)
        self.error = error
        self.executions: list[HarnessExecutionContext] = []

    def build(
        self,
        execution: HarnessExecutionContext,
    ) -> Sequence[CapabilityToolBinding]:
        self.executions.append(execution)
        if self.error is not None:
            raise self.error
        return self.bindings


class ToolCallingModel:
    def __init__(self, *, usage: ModelUsage | None = None) -> None:
        self.usage = usage or ModelUsage()
        self.tool_results: list[tuple[FunctionResult, ...]] = []

    async def complete(
        self,
        request: HarnessRequest,
        tool_results: tuple[FunctionResult, ...],
    ) -> ModelResponse:
        del request
        self.tool_results.append(tool_results)
        if not tool_results:
            return ModelResponse(
                tool_calls=(
                    FunctionCall(
                        id="call-1",
                        name="read_file",
                        arguments={"path": "app/**"},
                    ),
                ),
                usage=self.usage,
            )
        return ModelResponse(content="verified model result", usage=self.usage)


class RecordingModelFactory:
    def __init__(self, model: ToolCallingModel | None = None) -> None:
        self.model = model or ToolCallingModel()
        self.tool_sets: list[list[FunctionTool]] = []

    def build(self, tools: Sequence[FunctionTool]) -> ToolCallingModel:
        self.tool_sets.append(list(tools))
        return self.model


class RunnerCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_factory_binds_only_callable_capabilities_for_one_attempt(
        self,
    ) -> None:
        binding_factory = RecordingBindingFactory([read_binding()])
        model_factory = RecordingModelFactory()
        harness = A2AInboundHarnessFactory(
            model_factory,
            binding_factory,
        ).build(valid_execution_context())

        result = await harness.execute(
            HarnessRequest(
                code="bounded inbound context",
                language="text",
                timeout=5,
                execution=HarnessExecutionContext(
                    mission_id="mis-inbound",
                    work_unit_id="wu-inbound",
                    attempt=2,
                ),
            )
        )

        self.assertTrue(result.sandbox.success)
        self.assertEqual(result.sandbox.stdout, "verified model result")
        self.assertEqual(
            binding_factory.executions,
            [
                HarnessExecutionContext(
                    mission_id="mis-inbound",
                    work_unit_id="wu-inbound",
                    attempt=2,
                )
            ],
        )
        self.assertEqual(
            [tool.name for tool in model_factory.tool_sets[0]],
            ["read_file"],
        )
        self.assertEqual(
            model_factory.model.tool_results[1][0].content,
            "read:app/**:app/**",
        )

    def test_factory_fails_when_callable_capability_has_no_binding(self) -> None:
        with self.assertRaisesRegex(
            ClaimedWorkResolutionError,
            "claimed capability requirements could not be resolved",
        ):
            A2AInboundHarnessFactory(
                RecordingModelFactory(),
                RecordingBindingFactory(),
            ).build(valid_execution_context())

    def test_factory_sanitizes_binding_factory_failures(self) -> None:
        with self.assertRaisesRegex(
            ClaimedWorkResolutionError,
            "capability binding factory failed: RuntimeError",
        ) as raised:
            A2AInboundHarnessFactory(
                RecordingModelFactory(),
                RecordingBindingFactory(error=RuntimeError("provider-secret")),
            ).build(valid_execution_context())
        self.assertNotIn("provider-secret", str(raised.exception))

    async def test_contract_model_cost_caps_the_request_scoped_harness(self) -> None:
        context = valid_execution_context()
        context["contract"]["budgets"]["modelCost"] = 0.5
        model = ToolCallingModel(usage=ModelUsage(cost=0.6))
        harness = A2AInboundHarnessFactory(
            RecordingModelFactory(model),
            RecordingBindingFactory([read_binding()]),
        ).build(context)

        result = await harness.execute(
            HarnessRequest(code="context", language="text", timeout=5)
        )

        self.assertFalse(result.sandbox.success)
        self.assertEqual(result.sandbox.error, "Harness model-cost budget exhausted")
        self.assertEqual(result.tool_calls, 0)

    async def test_composition_root_executes_claim_with_request_scoped_harness(
        self,
    ) -> None:
        context = valid_execution_context()
        context["contract"]["allowedCapabilities"] = [
            {"capability": "a2a.receive", "scope": {}}
        ]
        context["workUnit"]["requiredCapabilities"] = ["a2a.receive"]

        class FinalModel(ToolCallingModel):
            async def complete(
                self,
                request: HarnessRequest,
                tool_results: tuple[FunctionResult, ...],
            ) -> ModelResponse:
                del request, tool_results
                return ModelResponse(content="inbound final output")

        class Control:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def claim_work_unit(self, mission_id: str, **kwargs: Any):
                del mission_id, kwargs
                self.calls.append("claim")
                return {"workUnit": inbound_claim_payload()}

            async def get_execution_context(
                self, mission_id: str, work_unit_id: str, **kwargs: Any
            ):
                del mission_id, work_unit_id, kwargs
                self.calls.append("context")
                return {"executionContext": context}

            async def start_work_unit(
                self, mission_id: str, work_unit_id: str, **kwargs: Any
            ):
                del mission_id, work_unit_id, kwargs
                self.calls.append("start")
                return inbound_claim_payload()

            async def record_execution_checkpoint(
                self,
                mission_id: str,
                work_unit_id: str,
                **kwargs: Any,
            ):
                self.calls.append(f"checkpoint:{kwargs['sequence']}")
                return {
                    "id": kwargs["checkpoint_id"],
                    "missionId": mission_id,
                    "workUnitId": work_unit_id,
                    "attempt": 2,
                    "sequence": kwargs["sequence"],
                    "phase": kwargs["phase"],
                    "iteration": kwargs["iteration"],
                    "toolCalls": kwargs["tool_calls"],
                    "promptTokens": kwargs["prompt_tokens"],
                    "completionTokens": kwargs["completion_tokens"],
                    "modelCost": kwargs["model_cost"],
                    "terminal": kwargs["terminal"],
                    "failureReason": kwargs.get("failure_reason"),
                    "stateDigest": "sha256:" + "a" * 64,
                    "createdBy": {"id": "runner-1", "type": "service"},
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                }

            async def heartbeat_work_unit(self, *args: Any, **kwargs: Any):
                del args, kwargs
                return inbound_claim_payload()

            async def register_artifact(self, *args: Any, **kwargs: Any):
                del args, kwargs
                self.calls.append("register")
                return {"id": "artifact-1"}

            async def complete_work_unit(self, *args: Any, **kwargs: Any):
                del args, kwargs
                self.calls.append("complete")
                return {"id": "wu-inbound", "status": "VERIFYING"}

            async def fail_work_unit(self, *args: Any, **kwargs: Any):
                del args, kwargs
                self.calls.append("fail")
                return {"id": "wu-inbound", "status": "FAILED"}

        control = Control()
        publisher = FakePublisher()
        model_factory = RecordingModelFactory(FinalModel())
        runner = build_a2a_inbound_runner(
            control,
            publisher=publisher,
            model_factory=model_factory,
            binding_factory=RecordingBindingFactory(),
            runner_id="runner-1",
            assigned_agent_id="reviewer",
            assigned_adapter="local_codex",
        )

        result = await runner.claim_and_run("mis-inbound")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.success)
        self.assertEqual(publisher.contents, [b"inbound final output"])
        self.assertEqual(
            control.calls,
            [
                "claim",
                "context",
                "start",
                "checkpoint:1",
                "checkpoint:2",
                "checkpoint:3",
                "checkpoint:4",
                "checkpoint:5",
                "register",
                "complete",
            ],
        )
        self.assertEqual(model_factory.tool_sets, [[]])

    def test_composition_root_rejects_recursive_outbound_adapter(self) -> None:
        with self.assertRaisesRegex(ValueError, "outbound A2A adapter"):
            build_a2a_inbound_runner(
                object(),  # type: ignore[arg-type]
                publisher=FakePublisher(),
                model_factory=RecordingModelFactory(),
                binding_factory=RecordingBindingFactory(),
                runner_id="runner-1",
                assigned_agent_id="reviewer",
                assigned_adapter="a2a.outbound",
            )


if __name__ == "__main__":
    unittest.main()
