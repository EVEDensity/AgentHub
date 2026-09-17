from __future__ import annotations

from pathlib import Path
import tempfile

import unittest

from app.services.tool_executor import ToolExecutor
from app.services.tool_registry import ToolDefinition, ToolParameter, tool_registry
from app.services.tools.receipts import ToolReceiptStore


class ToolExecutorReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_executor_uses_execution_receipt_when_key_is_supplied(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmp_path = Path(tmp.name)
        calls: list[str] = []

    async def handler(value: str) -> dict[str, object]:
        calls.append(value)
        return {"success": True, "result": value}

        tool_registry.register(
            ToolDefinition(
                name="receipt_test_tool",
                description="test",
                category="system",
                parameters=[ToolParameter("value", "string", True, "value")],
                return_type="object",
                examples=[],
                handler=handler,
            )
        )
        try:
            executor = ToolExecutor()
            executor.configure(receipt_store=ToolReceiptStore(tmp_path / "receipts.json"))
            key = "mission-1/work-1/1/call-1"

            first = await executor.execute("receipt_test_tool", {"value": "ok"}, idempotency_key=key)
            second = await executor.execute("receipt_test_tool", {"value": "ok"}, idempotency_key=key)

            self.assertTrue(first["success"])
            self.assertTrue(second["recovered"])
            self.assertEqual(calls, ["ok"])
        finally:
            tool_registry.unregister("receipt_test_tool")
