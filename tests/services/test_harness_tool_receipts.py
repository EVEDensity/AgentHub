from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


from app.services.harness_service import (
    FunctionCall,
    FunctionCallingHarness,
    FunctionTool,
    HarnessExecutionContext,
    HarnessRequest,
    ModelResponse,
    build_tool_idempotency_key,
)
from app.services.model_contract import ToolResult
from app.services.tool_executor import ToolExecutor
from app.services.tools.receipts import ToolReceiptStatus, ToolReceiptStore


class _Model:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses

    async def complete(self, request: Any, tool_results: tuple[ToolResult, ...], *, tools_enabled: bool = True) -> ModelResponse:
        del request, tool_results, tools_enabled
        return self.responses.pop(0)


def _validate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    value = arguments.get("value")
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    return {"value": value}


class HarnessToolReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_harness_routes_failure_through_receipt_store(self) -> None:
        async def explode(arguments: Mapping[str, Any]) -> str:
            del arguments
            raise RuntimeError("boom")

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            store = ToolReceiptStore(tmp_path / "receipts.json")
            executor = ToolExecutor()
            execution = HarnessExecutionContext("mission-1", "work-1", 2)
            harness = FunctionCallingHarness(
            _Model([
                ModelResponse(tool_calls=(FunctionCall(id="call-1", name="explode", arguments={"value": "x"}),)),
                ModelResponse(content="done"),
            ]),
                [FunctionTool("explode", explode, _validate)],
                tool_executor=executor,
            )
            executor.configure(receipt_store=store)

            result = await harness.execute(HarnessRequest("run", "text", 5, execution=execution))

            assert result.sandbox.success
            key = build_tool_idempotency_key(execution, "explode", {"value": "x"})
            receipt = store.get(key)
            assert receipt is not None
            assert receipt.status is ToolReceiptStatus.FAILED


    async def test_callable_receipt_blocks_duplicate_execution(self) -> None:
        calls = 0

        async def handler(arguments: Mapping[str, Any]) -> str:
            nonlocal calls
            calls += 1
            return str(arguments["value"])

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            store = ToolReceiptStore(tmp_path / "receipts.json")
            executor = ToolExecutor()
            executor.configure(receipt_store=store)
            key = build_tool_idempotency_key(None, "echo", {"value": "x"})

            first = await executor.execute_callable("echo", {"value": "x"}, handler, idempotency_key=key)
            second = await executor.execute_callable("echo", {"value": "x"}, handler, idempotency_key=key)

            assert first["success"] is True
            assert second["recovered"] is True
            assert calls == 1
