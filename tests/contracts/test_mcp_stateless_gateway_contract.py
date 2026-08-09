from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.mcp_tool_adapter import (
    MCPCallContext,
    MCPProtocolError,
    MCPToolCall,
    StatelessMCPClient,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MCP_GATEWAY_ROOT = REPOSITORY_ROOT / "services" / "go" / "mcp-gateway"
JWT_SECRET = b"mcp-contract-test-secret-32-bytes"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _access_token() -> str:
    now = int(time.time())
    header = _base64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    claims = _base64url(
        json.dumps(
            {
                "iss": "iam-service",
                "sub": "actor-contract",
                "tenant_id": "tenant-contract",
                "user_id": "actor-contract",
                "scopes": ["tool:execute", "document:read"],
                "iat": now,
                "nbf": now,
                "exp": now + 300,
            }
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    signature = _base64url(hmac.new(JWT_SECRET, signing_input, hashlib.sha256).digest())
    return f"{header}.{claims}.{signature}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


class StatelessMCPGatewayContractTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("go") is None:
            raise unittest.SkipTest("Go toolchain is required for the MCP contract test")

        cls._temporary = tempfile.TemporaryDirectory(prefix="agenthub-mcp-contract-")
        temporary = Path(cls._temporary.name)
        binary = temporary / ("mcp-gateway.exe" if os.name == "nt" else "mcp-gateway")
        build_env = os.environ.copy()
        build_env["GOWORK"] = "off"
        build_env["GOCACHE"] = str(temporary / "go-build-cache")
        subprocess.run(
            ["go", "build", "-o", str(binary), "./cmd/mcp-gateway"],
            cwd=MCP_GATEWAY_ROOT,
            env=build_env,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        fail_closed_env = os.environ.copy()
        fail_closed_env.update(
            {"MCP_TRANSPORT": "sse", "MCP_ADDR": "127.0.0.1:0"}
        )
        fail_closed_env.pop("JWT_SECRET", None)
        fail_closed_env.pop("MCP_ALLOW_INSECURE_DEV_AUTH", None)
        failed_start = subprocess.run(
            [str(binary)],
            cwd=MCP_GATEWAY_ROOT,
            env=fail_closed_env,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if (
            failed_start.returncode == 0
            or "JWT_SECRET is required" not in failed_start.stderr
        ):
            raise RuntimeError("MCP Gateway did not fail closed without JWT_SECRET")

        cls._port = _free_port()
        runtime_env = os.environ.copy()
        runtime_env.update(
            {
                "MCP_TRANSPORT": "sse",
                "MCP_ADDR": f"127.0.0.1:{cls._port}",
                "JWT_SECRET": JWT_SECRET.decode("ascii"),
                "KNOWLEDGE_URL": "http://127.0.0.1:1",
                "GATEWAY_URL": "http://127.0.0.1:1",
            }
        )
        cls._process = subprocess.Popen(
            [str(binary)],
            cwd=MCP_GATEWAY_ROOT,
            env=runtime_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        health_url = f"http://127.0.0.1:{cls._port}/healthz"
        for _ in range(50):
            if cls._process.poll() is not None:
                raise RuntimeError("MCP Gateway exited before becoming healthy")
            try:
                with urllib.request.urlopen(health_url, timeout=0.2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("MCP Gateway did not become healthy")

    @classmethod
    def tearDownClass(cls) -> None:
        process = getattr(cls, "_process", None)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        temporary = getattr(cls, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()

    async def test_python_client_calls_authenticated_go_rpc(self) -> None:
        endpoint = f"http://127.0.0.1:{self._port}/mcp/rpc"
        client = StatelessMCPClient(endpoint, access_token=_access_token(), timeout=10)
        result = await client.call_tool(
            MCPToolCall(
                context=MCPCallContext(
                    execution=HarnessExecutionContext(
                        mission_id="mission-contract",
                        work_unit_id="work-unit-contract",
                        attempt=1,
                    ),
                    capability="platform.health",
                    scope={
                        "required_scope": "document:read",
                        "tenant_id": "tenant-contract",
                    },
                ),
                name="system_health",
            )
        )
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content)["status"], "ok")

    async def test_go_rpc_rejects_invalid_token(self) -> None:
        endpoint = f"http://127.0.0.1:{self._port}/mcp/rpc"
        client = StatelessMCPClient(endpoint, access_token="invalid", timeout=10)
        with self.assertRaisesRegex(MCPProtocolError, "HTTP 401"):
            await client.call_tool(
                MCPToolCall(
                    context=MCPCallContext(
                        execution=HarnessExecutionContext("mission-contract", "work-unit-contract", 1),
                        capability="platform.health",
                    ),
                    name="system_health",
                )
            )

    async def test_go_rpc_rejects_tool_outside_declared_capability(self) -> None:
        endpoint = f"http://127.0.0.1:{self._port}/mcp/rpc"
        client = StatelessMCPClient(endpoint, access_token=_access_token(), timeout=10)
        with self.assertRaisesRegex(MCPProtocolError, "MCP tool call returned an error"):
            await client.call_tool(
                MCPToolCall(
                    context=MCPCallContext(
                        execution=HarnessExecutionContext("mission-contract", "work-unit-contract", 1),
                        capability="knowledge.search",
                        scope={
                            "required_scope": "document:read",
                            "tenant_id": "tenant-contract",
                        },
                    ),
                    name="system_health",
                )
            )


if __name__ == "__main__":
    unittest.main()
