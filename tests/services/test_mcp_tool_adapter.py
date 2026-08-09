from __future__ import annotations

import json
import unittest
from typing import Any

import httpx

from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.harness_service import (
    FunctionCallingHarness,
    FunctionResult,
    HarnessRequest,
    ModelResponse,
)
from app.services.mcp_tool_adapter import (
    InMemoryMCPAuditPort,
    MCPAdapterError,
    MCPCallContext,
    MCPProtocolError,
    MCPToolCall,
    MCPToolResult,
    StatelessMCPClient,
    StatelessMCPToolAdapter,
)


def _context(*, attempt: int = 1) -> MCPCallContext:
    return MCPCallContext(
        execution=HarnessExecutionContext(
            mission_id="mis-1",
            work_unit_id="wu-1",
            attempt=attempt,
        ),
        capability="workspace.read",
        scope={"root": "repo"},
        trace_id="trace-1",
    )


class MCPToolAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_context_and_client_configuration_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            MCPCallContext(execution=_context().execution, capability=" ")
        with self.assertRaises(ValueError):
            StatelessMCPClient("/relative/mcp")
        with self.assertRaises(ValueError):
            StatelessMCPClient("http://mcp.test/rpc", timeout=0)

    async def test_stateless_client_sends_context_on_every_call_and_audits_metadata(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "content": [{"type": "text", "text": "ok"}],
                    },
                },
            )

        audit = InMemoryMCPAuditPort()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            mcp = StatelessMCPClient(
                "http://mcp.test/rpc",
                http_client=client,
                access_token="token-1",
                audit=audit,
            )
            first = await mcp.call_tool(
                MCPToolCall(context=_context(), name="read_file", arguments={"path": "a"})
            )
            second = await mcp.call_tool(
                MCPToolCall(
                    context=_context(attempt=2),
                    name="read_file",
                    arguments={"path": "b"},
                )
            )

        self.assertEqual(first, MCPToolResult(content="ok"))
        self.assertEqual(second, MCPToolResult(content="ok"))
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(audit.events), 2)
        self.assertEqual(
            [json.loads(request.content)["method"] for request in requests],
            ["tools/call", "tools/call"],
        )
        self.assertNotIn(b"sessionId", requests[0].url.query)
        self.assertEqual(requests[0].headers["x-agenthub-mission-id"], "mis-1")
        self.assertEqual(requests[0].headers["x-agenthub-work-unit-id"], "wu-1")
        self.assertEqual(requests[0].headers["x-agenthub-attempt"], "1")
        self.assertEqual(requests[1].headers["x-agenthub-attempt"], "2")
        self.assertEqual(requests[0].headers["x-agenthub-capability"], "workspace.read")
        self.assertEqual(
            json.loads(requests[0].headers["x-agenthub-capability-scope"]),
            {"root": "repo"},
        )
        self.assertEqual(audit.events[0].tool_name, "read_file")
        self.assertTrue(audit.events[0].success)
        self.assertIsNone(audit.events[0].error_type)

    async def test_protocol_and_http_failures_are_audited_without_remote_details(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(502, text="upstream secret")

        audit = InMemoryMCPAuditPort()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            mcp = StatelessMCPClient(
                "http://mcp.test/rpc",
                http_client=client,
                audit=audit,
            )
            with self.assertRaisesRegex(MCPProtocolError, "HTTP 502"):
                await mcp.call_tool(MCPToolCall(context=_context(), name="read_file"))

        self.assertEqual(len(audit.events), 1)
        self.assertFalse(audit.events[0].success)
        self.assertEqual(audit.events[0].error_type, "MCPProtocolError")

    async def test_malformed_json_rpc_result_is_rejected_and_audited(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": []},
            )

        audit = InMemoryMCPAuditPort()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            mcp = StatelessMCPClient(
                "http://mcp.test/rpc",
                http_client=client,
                audit=audit,
            )
            with self.assertRaisesRegex(MCPProtocolError, "result must be an object"):
                await mcp.call_tool(MCPToolCall(context=_context(), name="read_file"))

        self.assertFalse(audit.events[0].success)
        self.assertEqual(audit.events[0].error_type, "MCPProtocolError")

    async def test_function_tool_adapter_returns_mcp_error_as_harness_feedback(self) -> None:
        class ErrorClient:
            async def call_tool(self, request: MCPToolCall) -> MCPToolResult:
                del request
                return MCPToolResult(content="denied", is_error=True)

        tool = StatelessMCPToolAdapter(
            ErrorClient(),
            name="read_file",
            capability="workspace.read",
            execution=_context().execution,
            scope={"root": "repo"},
        ).as_function_tool()

        class Model:
            def __init__(self) -> None:
                self.feedback: tuple[FunctionResult, ...] = ()

            async def complete(
                self,
                request: HarnessRequest,
                tool_results: tuple[FunctionResult, ...],
            ) -> ModelResponse:
                del request
                self.feedback = tool_results
                if not tool_results:
                    from app.services.harness_service import FunctionCall

                    return ModelResponse(
                        tool_calls=(
                            FunctionCall(
                                id="call-1",
                                name="read_file",
                                arguments={"path": "a"},
                            ),
                        )
                    )
                return ModelResponse(content="handled")

        model = Model()
        result = await FunctionCallingHarness(model, [tool]).execute(
            HarnessRequest(code="read", language="text", timeout=1)
        )

        self.assertTrue(result.sandbox.success)
        self.assertFalse(model.feedback[0].success)
        self.assertIn("MCPAdapterError", model.feedback[0].content)

    async def test_audit_failure_is_fail_closed(self) -> None:
        class FailingAudit:
            async def record(self, event: Any) -> None:
                del event
                raise RuntimeError("audit unavailable")

        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": {"content": []}},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            mcp = StatelessMCPClient(
                "http://mcp.test/rpc",
                http_client=client,
                audit=FailingAudit(),
            )
            with self.assertRaisesRegex(MCPAdapterError, "audit recording failed"):
                await mcp.call_tool(MCPToolCall(context=_context(), name="read_file"))
