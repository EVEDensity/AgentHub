from __future__ import annotations

import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.harness_service import FunctionTool

if TYPE_CHECKING:
    from app.services.capability_tools import CapabilityToolBinding


class MCPAdapterError(RuntimeError):
    """Raised when a stateless MCP call cannot be completed safely."""


class MCPProtocolError(MCPAdapterError):
    """Raised when an MCP endpoint returns an invalid or error response."""


@dataclass(frozen=True)
class MCPCallContext:
    """Per-call identity and capability scope forwarded to the MCP server."""

    execution: HarnessExecutionContext
    capability: str
    scope: Mapping[str, Any] = field(default_factory=dict)
    trace_id: str = ""

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("MCP capability must be non-empty")
        if not isinstance(self.scope, Mapping):
            raise TypeError("MCP capability scope must be an object")
        object.__setattr__(self, "scope", MappingProxyType(dict(self.scope)))


@dataclass(frozen=True)
class MCPToolCall:
    """One JSON-RPC tools/call request with no session state."""

    context: MCPCallContext
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("MCP tool name must be non-empty")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("MCP tool arguments must be an object")
        object.__setattr__(self, "arguments", dict(self.arguments))


@dataclass(frozen=True)
class MCPToolResult:
    """Normalized MCP tool output; content is retained only for this call."""

    content: str
    is_error: bool = False


@dataclass(frozen=True)
class MCPToolAuditEvent:
    """Content-free audit metadata for one adapter call."""

    request_id: int
    context: MCPCallContext
    tool_name: str
    success: bool
    duration_ms: int
    error_type: str | None = None


class MCPClientPort(Protocol):
    async def call_tool(self, request: MCPToolCall) -> MCPToolResult: ...


class MCPAuditPort(Protocol):
    async def record(self, event: MCPToolAuditEvent) -> None: ...


class InMemoryMCPAuditPort:
    """Request-independent in-memory audit sink for tests and local inspection."""

    def __init__(self) -> None:
        self.events: list[MCPToolAuditEvent] = []

    async def record(self, event: MCPToolAuditEvent) -> None:
        self.events.append(event)


class StatelessMCPClient(MCPClientPort):
    """Call an MCP HTTP endpoint without initialize/session state."""

    def __init__(
        self,
        endpoint: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        access_token: str | None = None,
        timeout: float = 30.0,
        audit: MCPAuditPort | None = None,
    ) -> None:
        parsed = httpx.URL(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("MCP endpoint must be an absolute HTTP(S) URL")
        if timeout <= 0:
            raise ValueError("MCP timeout must be positive")
        self._endpoint = endpoint
        self._http_client = http_client
        self._access_token = access_token
        self._timeout = timeout
        self._audit = audit

    async def call_tool(self, request: MCPToolCall) -> MCPToolResult:
        request_id = secrets.randbits(63)
        started_at = time.monotonic()
        try:
            result = await self._call(request_id, request)
        except MCPAdapterError as exc:
            await self._record_audit(
                request_id,
                request,
                success=False,
                started_at=started_at,
                error_type=type(exc).__name__,
            )
            raise
        except Exception as exc:
            error = MCPAdapterError(f"MCP tool call failed: {type(exc).__name__}")
            await self._record_audit(
                request_id,
                request,
                success=False,
                started_at=started_at,
                error_type=type(exc).__name__,
            )
            raise error from exc

        await self._record_audit(
            request_id,
            request,
            success=not result.is_error,
            started_at=started_at,
            error_type="MCPToolError" if result.is_error else None,
        )
        return result

    async def _call(self, request_id: int, request: MCPToolCall) -> MCPToolResult:
        headers = self._headers(request.context)
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": request.name,
                "arguments": dict(request.arguments),
            },
        }
        if self._http_client is None:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._endpoint,
                    json=payload,
                    headers=headers,
                )
        else:
            response = await self._http_client.post(
                self._endpoint,
                json=payload,
                headers=headers,
                timeout=self._timeout,
            )
        if response.status_code >= 400:
            raise MCPProtocolError(f"MCP endpoint returned HTTP {response.status_code}")
        try:
            envelope = response.json()
        except ValueError as exc:
            raise MCPProtocolError("MCP endpoint returned invalid JSON") from exc
        if not isinstance(envelope, Mapping):
            raise MCPProtocolError("MCP endpoint returned a non-object response")
        response_id = envelope.get("id")
        if response_id != request_id:
            raise MCPProtocolError("MCP response id did not match the request")
        error = envelope.get("error")
        if isinstance(error, Mapping):
            message = error.get("message")
            raise MCPProtocolError(
                "MCP tool call returned an error"
                + (f": {message}" if isinstance(message, str) else "")
            )
        return _normalize_tool_result(envelope.get("result"))

    def _headers(self, context: MCPCallContext) -> dict[str, str]:
        try:
            scope = json.dumps(dict(context.scope), ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise MCPAdapterError("MCP capability scope is not JSON serializable") from exc
        headers = {
            "content-type": "application/json",
            "x-agenthub-mission-id": context.execution.mission_id,
            "x-agenthub-work-unit-id": context.execution.work_unit_id,
            "x-agenthub-attempt": str(context.execution.attempt),
            "x-agenthub-capability": context.capability,
            "x-agenthub-capability-scope": scope,
        }
        if context.trace_id:
            headers["x-agenthub-trace-id"] = context.trace_id
        if self._access_token:
            headers["authorization"] = f"Bearer {self._access_token}"
        return headers

    async def _record_audit(
        self,
        request_id: int,
        request: MCPToolCall,
        *,
        success: bool,
        started_at: float,
        error_type: str | None,
    ) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.record(
                MCPToolAuditEvent(
                    request_id=request_id,
                    context=request.context,
                    tool_name=request.name,
                    success=success,
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                    error_type=error_type,
                )
            )
        except Exception as exc:
            raise MCPAdapterError("MCP audit recording failed") from exc


class StatelessMCPToolAdapter:
    """Bind one MCP tool to Harness FunctionTool for one execution context."""

    def __init__(
        self,
        client: MCPClientPort,
        *,
        name: str,
        capability: str,
        execution: HarnessExecutionContext,
        scope: Mapping[str, Any] | None = None,
        description: str = "",
        parameters: Mapping[str, Any] | None = None,
    ) -> None:
        if not name.strip():
            raise ValueError("MCP tool name must be non-empty")
        self._client = client
        self._context = MCPCallContext(
            execution=execution,
            capability=capability,
            scope=scope or {},
        )
        self._name = name
        self._description = description
        self._parameters = dict(parameters or {})

    def as_function_tool(self) -> FunctionTool:
        async def handler(arguments: Mapping[str, Any]) -> str:
            result = await self._client.call_tool(
                MCPToolCall(
                    context=self._context,
                    name=self._name,
                    arguments=arguments,
                )
            )
            if result.is_error:
                raise MCPAdapterError("MCP tool returned an error")
            return result.content

        def validate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
            if not isinstance(arguments, Mapping):
                raise TypeError("MCP tool arguments must be an object")
            return dict(arguments)

        return FunctionTool(
            name=self._name,
            description=self._description,
            parameters=self._parameters,
            validate_arguments=validate,
            handler=handler,
        )


def build_mcp_capability_binding(
    client: MCPClientPort,
    *,
    capability: str,
    function_name: str,
    execution: HarnessExecutionContext,
    description: str = "",
    parameters: Mapping[str, Any] | None = None,
    trace_id: str = "",
) -> CapabilityToolBinding:
    """Create a CapabilityToolBinding that forwards the resolver's scope.

    The return type is kept local to avoid making the capability registry depend
    on the MCP adapter module at import time.
    """
    from app.services.capability_tools import CapabilityToolBinding

    if not capability.strip():
        raise ValueError("MCP capability must be non-empty")
    if not function_name.strip():
        raise ValueError("MCP function name must be non-empty")

    def validate(
        arguments: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del scope
        if not isinstance(arguments, Mapping):
            raise TypeError("MCP tool arguments must be an object")
        return dict(arguments)

    async def handle(
        arguments: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> str:
        result = await client.call_tool(
            MCPToolCall(
                context=MCPCallContext(
                    execution=execution,
                    capability=capability,
                    scope=scope,
                    trace_id=trace_id,
                ),
                name=function_name,
                arguments=arguments,
            )
        )
        if result.is_error:
            raise MCPAdapterError("MCP tool returned an error")
        return result.content

    return CapabilityToolBinding(
        capability=capability,
        function_name=function_name,
        description=description,
        parameters=dict(parameters or {}),
        validate_arguments=validate,
        handler=handle,
    )


def _normalize_tool_result(raw: object) -> MCPToolResult:
    if not isinstance(raw, Mapping):
        raise MCPProtocolError("MCP response result must be an object")
    is_error = raw.get("isError", False)
    if not isinstance(is_error, bool):
        raise MCPProtocolError("MCP response isError must be a boolean")
    content = raw.get("content", [])
    if not isinstance(content, list):
        raise MCPProtocolError("MCP response content must be an array")
    text_blocks: list[str] = []
    for block in content:
        if not isinstance(block, Mapping):
            raise MCPProtocolError("MCP response content block must be an object")
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text_blocks.append(block["text"])
        else:
            text_blocks.append(json.dumps(block, ensure_ascii=False, sort_keys=True))
    return MCPToolResult(content="\n".join(text_blocks), is_error=is_error)


__all__ = [
    "InMemoryMCPAuditPort",
    "MCPAdapterError",
    "MCPAuditPort",
    "MCPCallContext",
    "MCPClientPort",
    "MCPProtocolError",
    "MCPToolAuditEvent",
    "MCPToolCall",
    "MCPToolResult",
    "StatelessMCPClient",
    "StatelessMCPToolAdapter",
    "build_mcp_capability_binding",
]
