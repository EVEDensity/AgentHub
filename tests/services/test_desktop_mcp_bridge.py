"""P2-1 MCP tool bridge: stdio JSON-RPC round trip + crash degradation."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from app.services.desktop_mcp_bridge import (
    DesktopMcpBridge,
    McpBridgeError,
    McpServerConfig,
    load_mcp_server_configs,
)

FAKE_SERVER = """import json, sys
while True:
    line = sys.stdin.readline()
    if not line:
        break
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method = msg.get("method")
    if method == "initialize":
        resp = {
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "fake", "version": "1"},
            },
        }
    elif method == "tools/list":
        resp = {
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo the input text",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    },
                    {
                        "name": "fail",
                        "description": "Always errors",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "big",
                        "description": "Returns oversized text",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                ]
            },
        }
    elif method == "tools/call":
        name = msg["params"]["name"]
        if name == "fail":
            resp = {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "boom"}],
                },
            }
        elif name == "big":
            resp = {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {
                    "content": [{"type": "text", "text": "x" * 5000}],
                },
            }
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {
                    "content": [
                        {"type": "text", "text": "echo:" + msg["params"]["arguments"]["text"]}
                    ]
                },
            }
    else:
        continue
    sys.stdout.write(json.dumps(resp, ensure_ascii=True) + "\\n")
    sys.stdout.flush()
"""

CRASHING_SERVER = "import sys; sys.exit(1)"


def _server_command(script: str, tmp_dir: Path, name: str = "fake_mcp_server.py") -> list[str]:
    script_path = tmp_dir / name
    script_path.write_text(script, encoding="utf-8")
    return [sys.executable, str(script_path)]


class McpConfigParsingTests(unittest.TestCase):
    def test_loads_list_config_with_command_and_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "mcp.json"
            config_path.write_text(
                json.dumps(
                    [
                        {"name": "fs", "command": ["python", "-m", "server"]},
                        {"name": "remote", "url": "http://127.0.0.1:9300/mcp"},
                    ]
                ),
                encoding="utf-8",
            )
            configs = load_mcp_server_configs(config_path)
        self.assertEqual(
            configs,
            [
                McpServerConfig(name="fs", command=["python", "-m", "server"]),
                McpServerConfig(name="remote", url="http://127.0.0.1:9300/mcp"),
            ],
        )

    def test_invalid_config_shapes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_name = Path(tmp) / "a.json"
            missing_name.write_text(json.dumps([{"command": "run"}]), encoding="utf-8")
            with self.assertRaises(McpBridgeError):
                load_mcp_server_configs(missing_name)

            both = Path(tmp) / "b.json"
            both.write_text(
                json.dumps([{"name": "x", "command": "run", "url": "http://x"}]),
                encoding="utf-8",
            )
            with self.assertRaises(McpBridgeError):
                load_mcp_server_configs(both)

            neither = Path(tmp) / "c.json"
            neither.write_text(json.dumps([{"name": "x"}]), encoding="utf-8")
            with self.assertRaises(McpBridgeError):
                load_mcp_server_configs(neither)

            not_array = Path(tmp) / "d.json"
            not_array.write_text(json.dumps({"name": "x"}), encoding="utf-8")
            with self.assertRaises(McpBridgeError):
                load_mcp_server_configs(not_array)


class DesktopMcpBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_server_round_trip_list_and_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            command = _server_command(FAKE_SERVER, Path(tmp))
            bridge = DesktopMcpBridge(
                [McpServerConfig(name="fake", command=command)]
            )
            try:
                tools = await bridge.build_tools()
                self.assertEqual(
                    [tool.name for tool in tools],
                    ["mcp_fake_echo", "mcp_fake_fail", "mcp_fake_big"],
                )
                echo = tools[0]
                self.assertIn("[MCP:fake]", echo.description)
                # validate_arguments enforces the MCP inputSchema required list.
                with self.assertRaises(ValueError):
                    echo.validate_arguments({})
                validated = echo.validate_arguments({"text": "hello"})
                result = await echo.handler(validated)
                self.assertEqual(result, "echo:hello")

                # isError results surface as tool failures.
                failure = await tools[1].handler({})
                self.assertIn("工具执行失败", failure)
                self.assertIn("boom", failure)
            finally:
                await bridge.aclose()

    async def test_tool_result_is_truncated_to_desktop_cap(self) -> None:
        from app.services.desktop_mcp_bridge import MCP_TOOL_RESULT_MAX_CHARS

        with tempfile.TemporaryDirectory() as tmp:
            command = _server_command(FAKE_SERVER, Path(tmp))
            bridge = DesktopMcpBridge(
                [McpServerConfig(name="fake", command=command)]
            )
            try:
                tools = await bridge.build_tools()
                result = await tools[2].handler({})
                self.assertIn("...[截断]", result)
                self.assertLess(len(result), 5000)
                self.assertEqual(
                    MCP_TOOL_RESULT_MAX_CHARS,
                    4000,
                )
            finally:
                await bridge.aclose()

    async def test_crashing_server_degrades_to_no_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            command = _server_command(CRASHING_SERVER, Path(tmp))
            bridge = DesktopMcpBridge(
                [McpServerConfig(name="dead", command=command)]
            )
            tools = await bridge.build_tools()
            self.assertEqual(tools, [])
            await bridge.aclose()

    async def test_url_servers_are_skipped_without_failure(self) -> None:
        bridge = DesktopMcpBridge(
            [McpServerConfig(name="remote", url="http://127.0.0.1:9300/mcp")]
        )
        tools = await bridge.build_tools()
        self.assertEqual(tools, [])
        await bridge.aclose()

    async def test_mixed_servers_keep_working_ones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dead = _server_command(CRASHING_SERVER, Path(tmp), "dead_server.py")
            alive = _server_command(FAKE_SERVER, Path(tmp), "alive_server.py")
            bridge = DesktopMcpBridge(
                [
                    McpServerConfig(name="dead", command=dead),
                    McpServerConfig(name="fake", command=alive),
                ]
            )
            try:
                tools = await bridge.build_tools()
                self.assertTrue(all(tool.name.startswith("mcp_fake_") for tool in tools))
                self.assertEqual(len(tools), 3)
            finally:
                await bridge.aclose()


if __name__ == "__main__":
    unittest.main()
