"""MCP tool bridge for the desktop local runner (P2-1).

``AGENTHUB_DESKTOP_LOCAL_RUNNER_MCP_CONFIG`` may point at an ``mcp.json``
file describing optional MCP tool servers::

    [{"name": "files", "command": "npx -y some-mcp-server"},
     {"name": "remote", "url": "http://127.0.0.1:9300/mcp"}]

Every reachable stdio server is started as a child process, taken through
the minimal JSON-RPC handshake (newline-delimited MCP stdio transport:
``initialize`` → ``notifications/initialized`` → ``tools/list``) and its
tools are wrapped as Harness :class:`FunctionTool` entries that execute
through ``tools/call``. Tool output is truncated to the desktop result
cap. HTTP servers are skipped in this minimal loop. Any connection
failure degrades to "no tools from that server" with a warning — the
runner keeps starting without MCP.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.harness_service import FunctionTool

logger = logging.getLogger("agenthub.desktop_mcp")

MCP_CONFIG_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_MCP_CONFIG"
MCP_TOOL_RESULT_MAX_CHARS = 4000
MCP_PROTOCOL_VERSION = "2024-11-05"
_MCP_REQUEST_TIMEOUT_SECONDS = 10.0
_MCP_CLIENT_INFO = {"name": "agenthub-desktop-runner", "version": "1"}

_TRUNCATION_MARKER = "...[截断]"


class McpBridgeError(RuntimeError):
    """Raised when an MCP server cannot be connected or called."""


@dataclass(frozen=True)
class McpServerConfig:
    """One configured MCP tool server entry."""

    name: str
    command: str | Sequence[str] | None = None
    url: str | None = None


def load_mcp_server_configs(path: Path | str) -> list[McpServerConfig]:
    """Parse the mcp.json file into server configs; invalid shapes fail."""
    raw = Path(path).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise McpBridgeError("MCP config must be a JSON array of servers")
    configs: list[McpServerConfig] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, Mapping):
            raise McpBridgeError(f"MCP config server #{index} is not an object")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise McpBridgeError(f"MCP config server #{index} has no name")
        command = entry.get("command")
        url = entry.get("url")
        if command is not None and not isinstance(command, (str, list)):
            raise McpBridgeError(
                f"MCP config server '{name}' command must be a string or array"
            )
        if (command is None) == (url is None):
            raise McpBridgeError(
                f"MCP config server '{name}' needs exactly one of command|url"
            )
        configs.append(
            McpServerConfig(
                name=name,
                command=command,
                url=str(url) if url is not None else None,
            )
        )
    return configs


def _truncate(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    return (
        f"{content[:max_chars]}\n{_TRUNCATION_MARKER}"
        f"（已显示前 {max_chars} 字符，共 {len(content)} 字符）"
    )


class _StdioMcpSession:
    """Minimal newline-delimited JSON-RPC client for one stdio MCP server."""

    def __init__(self, command: str | Sequence[str]) -> None:
        self._command = _split_command(command)
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 0

    async def start(self) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (NotImplementedError, OSError) as exc:
            raise McpBridgeError(
                f"cannot start MCP server {self._command}: {exc}"
            ) from exc
        if self._process is None or self._process.stdin is None:
            raise McpBridgeError("MCP server process has no stdin")

    async def _send(self, message: Mapping[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        line = json.dumps(message, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()

    async def _receive(self, request_id: int) -> Mapping[str, Any]:
        assert self._process is not None and self._process.stdout is not None
        while True:
            line = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=_MCP_REQUEST_TIMEOUT_SECONDS,
            )
            if not line:
                raise McpBridgeError("MCP server closed stdout")
            try:
                message = json.loads(line.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(message, Mapping) and message.get("id") == request_id:
                return message

    async def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = dict(params)
        await self._send(message)
        response = await self._receive(request_id)
        if "error" in response:
            raise McpBridgeError(f"MCP {method} failed: {response['error']}")
        result = response.get("result")
        return result if isinstance(result, Mapping) else {}

    async def notify(self, method: str) -> None:
        await self._send({"jsonrpc": "2.0", "method": method})

    async def handshake(self) -> None:
        await self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _MCP_CLIENT_INFO,
            },
        )
        await self.notify("notifications/initialized")

    async def close(self) -> None:
        if self._process is None:
            return
        if self._process.returncode is None:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
        await self._process.wait()
        self._process = None


def _mcp_text_from_result(result: Mapping[str, Any]) -> str:
    """Extract model-facing text from a tools/call result payload."""
    if result.get("isError"):
        content = result.get("content")
        text = _mcp_content_text(content)
        raise McpBridgeError(text or "MCP tool reported an error")
    return _mcp_content_text(result.get("content"))


def _mcp_content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, Mapping) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _mcp_validator_factory(
    input_schema: Mapping[str, Any],
) -> Any:
    required = input_schema.get("required")
    required_names = {
        name for name in required if isinstance(name, str)
    } if isinstance(required, list) else set()

    def validate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping):
            raise TypeError("tool arguments must be an object")
        missing = required_names - set(arguments)
        if missing:
            raise ValueError(f"missing required argument: {sorted(missing)[0]}")
        return dict(arguments)

    return validate


class DesktopMcpBridge:
    """Own MCP sessions and expose their tools as Harness FunctionTools."""

    def __init__(
        self,
        configs: list[McpServerConfig],
        *,
        max_result_chars: int = MCP_TOOL_RESULT_MAX_CHARS,
    ) -> None:
        if max_result_chars < 1:
            raise ValueError("max_result_chars must be positive")
        self._configs = list(configs)
        self._max_result_chars = max_result_chars
        self._sessions: list[_StdioMcpSession] = []

    async def build_tools(self) -> list[FunctionTool]:
        """Connect every configured stdio server; degrade per server."""
        tools: list[FunctionTool] = []
        for config in self._configs:
            if config.command is None:
                logger.info(
                    "MCP server '%s' uses an unsupported transport (url=%s); skipped",
                    config.name,
                    config.url,
                )
                continue
            session = _StdioMcpSession(config.command)
            try:
                await session.start()
                await session.handshake()
                listing = await session.request("tools/list", {})
            except Exception as exc:  # noqa: BLE001 - degrade per server
                logger.warning(
                    "MCP server '%s' unavailable, continuing without it: %s",
                    config.name,
                    exc,
                )
                await session.close()
                continue
            self._sessions.append(session)
            tools.extend(
                self._wrap_server_tools(config.name, session, listing)
            )
        return tools

    def _wrap_server_tools(
        self,
        server_name: str,
        session: _StdioMcpSession,
        listing: Mapping[str, Any],
    ) -> list[FunctionTool]:
        raw_tools = listing.get("tools")
        if not isinstance(raw_tools, list):
            return []
        tools: list[FunctionTool] = []
        for entry in raw_tools:
            if not isinstance(entry, Mapping):
                continue
            tool_name = str(entry.get("name") or "").strip()
            if not tool_name:
                continue
            schema = entry.get("inputSchema")
            input_schema = schema if isinstance(schema, Mapping) else {}
            description = str(entry.get("description") or tool_name)
            parameters = (
                dict(input_schema)
                if input_schema.get("type") == "object"
                else {"type": "object", "properties": {}, "required": []}
            )
            tools.append(
                FunctionTool(
                    name=f"mcp_{server_name}_{tool_name}",
                    description=(
                        f"[MCP:{server_name}] {description}"
                    ),
                    parameters=parameters,
                    validate_arguments=_mcp_validator_factory(input_schema),
                    handler=self._build_call_executor(session, tool_name),
                )
            )
        return tools

    def _build_call_executor(
        self,
        session: _StdioMcpSession,
        tool_name: str,
    ):
        async def execute(arguments: Mapping[str, Any]) -> str:
            try:
                result = await session.request(
                    "tools/call",
                    {"name": tool_name, "arguments": dict(arguments)},
                )
                text = _mcp_text_from_result(result)
            except Exception as exc:  # noqa: BLE001 - tool failures are model feedback
                return f"工具执行失败: {exc}"
            return _truncate(text, self._max_result_chars)

        return execute

    async def aclose(self) -> None:
        for session in self._sessions:
            await session.close()
        self._sessions = []


def _split_command(command: str | Sequence[str]) -> list[str]:
    """Normalize the command into an argv list for ``create_subprocess_exec``.

    Strings are split with ``posix=False`` so Windows drive-letter paths
    survive; leftover wrapping quotes are stripped from each token.
    """
    if isinstance(command, str):
        return [
            token.strip('"') for token in shlex.split(command, posix=os.name != "nt")
        ]
    return [str(part) for part in command]


__all__ = [
    "MCP_CONFIG_ENV",
    "MCP_PROTOCOL_VERSION",
    "MCP_TOOL_RESULT_MAX_CHARS",
    "DesktopMcpBridge",
    "McpBridgeError",
    "McpServerConfig",
    "load_mcp_server_configs",
]
