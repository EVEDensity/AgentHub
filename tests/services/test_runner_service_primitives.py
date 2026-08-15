from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx
from pydantic import ValidationError

from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.mcp_tool_adapter import MCPToolCall, MCPToolResult
from services.python.runner_service.bindings import PerAttemptMCPBindingFactory
from services.python.runner_service.config import (
    MCPBindingManifest,
    RunnerServiceSettings,
    load_mcp_binding_manifest,
    read_secret_file,
)
from services.python.runner_service.gateway import (
    ModelGatewayError,
    StrictOpenAICompatiblePromptAdapter,
)


def _settings(**overrides: object) -> RunnerServiceSettings:
    root = Path.cwd().resolve()
    values: dict[str, object] = {
        "runner_id": "runner-1",
        "mission_id": "mission-1",
        "assigned_agent_id": "agent-1",
        "assigned_adapter": "local",
        "mission_control_url": "https://control.example.test",
        "mission_control_token_file": root / "control.token",
        "model_gateway_url": "https://models.example.test/v1",
        "model_gateway_token_file": root / "model.token",
        "model": "gpt-5-mini",
        "mcp_endpoint": "https://mcp.example.test/mcp/rpc",
        "mcp_token_file": root / "mcp.token",
        "mcp_bindings_file": root / "bindings.json",
        "artifact_local_root": root / "artifacts",
    }
    values.update(overrides)
    return RunnerServiceSettings(**values)  # type: ignore[arg-type]


class RunnerServiceConfigTests(unittest.TestCase):
    def test_requires_explicit_identity_and_network_configuration(self) -> None:
        with self.assertRaises(ValidationError):
            RunnerServiceSettings()  # type: ignore[call-arg]

    def test_rejects_mock_models_url_credentials_and_invalid_backoff(self) -> None:
        for overrides in (
            {"model": "mock-gpt"},
            {"mission_control_url": "https://user:secret@control.test"},
            {"mcp_endpoint": "https://mcp.test/rpc?token=secret"},
            {"idle_delay_seconds": 5.0, "max_delay_seconds": 1.0},
            {"assigned_adapter": "a2a.outbound"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(
                ValidationError
            ):
                _settings(**overrides)

    def test_reads_single_value_secret_and_rejects_multi_line_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "token"
            token_file.write_text("secret-value\n", encoding="utf-8")
            self.assertEqual(read_secret_file(token_file), "secret-value")

            token_file.write_text("first\nsecond\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "one non-empty value"):
                read_secret_file(token_file)

    def test_binding_manifest_is_versioned_unique_and_credential_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_file = Path(directory) / "bindings.json"
            manifest_file.write_text(
                json.dumps(
                    {
                        "schemaVersion": "agenthub.runner.mcp-bindings.v1",
                        "bindings": [
                            {
                                "capability": "repository.read",
                                "functionName": "read_repository",
                                "description": "Read an allowed repository path",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"path": {"type": "string"}},
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            manifest = load_mcp_binding_manifest(manifest_file)
            self.assertEqual(manifest.bindings[0].capability, "repository.read")

            manifest_file.write_text(
                json.dumps(
                    {
                        "schemaVersion": "agenthub.runner.mcp-bindings.v1",
                        "bindings": [],
                        "accessToken": "must-not-be-accepted",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValidationError):
                load_mcp_binding_manifest(manifest_file)


class StrictModelGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_resolved_tools_and_records_provider_usage(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "read_repository",
                                            "arguments": '{"path":"README.md"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        ) as client:
            adapter = StrictOpenAICompatiblePromptAdapter(
                client,
                endpoint="https://models.example.test/v1",
                access_token="model-secret",
                timeout_seconds=10,
                max_response_bytes=4096,
                max_output_tokens=512,
            )
            response = await adapter.execute_prompt(
                "inspect the repository",
                "gpt-5-mini",
                system_prompt="stay within the contract",
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "read_repository",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
            )

        self.assertIn("tool_calls", response)
        self.assertEqual(adapter.last_usage["prompt_tokens"], 12)
        self.assertEqual(len(requests), 1)
        self.assertEqual(
            str(requests[0].url),
            "https://models.example.test/v1/chat/completions",
        )
        self.assertEqual(requests[0].headers["authorization"], "Bearer model-secret")
        request_payload = json.loads(requests[0].content)
        self.assertEqual(request_payload["tool_choice"], "auto")
        self.assertEqual(request_payload["max_tokens"], 512)
        self.assertEqual(
            request_payload["tools"][0]["function"]["name"],
            "read_repository",
        )

    async def test_rejects_remote_error_content_and_oversized_responses(self) -> None:
        async def rejected(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                502,
                headers={"content-type": "application/json"},
                content=b'{"detail":"provider-key-is-secret"}',
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(rejected),
            follow_redirects=False,
        ) as client:
            adapter = StrictOpenAICompatiblePromptAdapter(
                client,
                endpoint="https://models.example.test/v1",
                access_token="token",
                timeout_seconds=10,
                max_response_bytes=128,
            )
            with self.assertRaises(ModelGatewayError) as context:
                await adapter.execute_prompt("x", "gpt-5-mini")
            self.assertEqual(str(context.exception), "AI Gateway returned HTTP 502")
            self.assertNotIn("provider-key", str(context.exception))

        async def oversized(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b"{" + (b"x" * 256) + b"}",
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(oversized),
            follow_redirects=False,
        ) as client:
            adapter = StrictOpenAICompatiblePromptAdapter(
                client,
                endpoint="https://models.example.test/v1",
                access_token="token",
                timeout_seconds=10,
                max_response_bytes=128,
            )
            with self.assertRaisesRegex(ModelGatewayError, "exceeded the limit"):
                await adapter.execute_prompt("x", "gpt-5-mini")

    async def test_rejects_malformed_tool_calls_instead_of_dropping_them(self) -> None:
        async def malformed(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "function": {
                                            "name": "read_repository",
                                            "arguments": "not-json",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(malformed),
            follow_redirects=False,
        ) as client:
            adapter = StrictOpenAICompatiblePromptAdapter(
                client,
                endpoint="https://models.example.test/v1",
                access_token="token",
                timeout_seconds=10,
                max_response_bytes=4096,
            )
            with self.assertRaisesRegex(ModelGatewayError, "arguments are invalid"):
                await adapter.execute_prompt("x", "gpt-5-mini")


class RecordingMCPClient:
    def __init__(self) -> None:
        self.calls: list[MCPToolCall] = []

    async def call_tool(self, request: MCPToolCall) -> MCPToolResult:
        self.calls.append(request)
        return MCPToolResult(content="ok")


class PerAttemptMCPBindingFactoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_captures_exact_attempt_without_manifest_credentials(self) -> None:
        manifest = MCPBindingManifest.model_validate(
            {
                "schemaVersion": "agenthub.runner.mcp-bindings.v1",
                "bindings": [
                    {
                        "capability": "repository.read",
                        "functionName": "read_repository",
                        "parameters": {"type": "object"},
                    }
                ],
            }
        )
        client = RecordingMCPClient()
        execution = HarnessExecutionContext(
            mission_id="mission-1",
            work_unit_id="work-1",
            attempt=2,
        )
        bindings = PerAttemptMCPBindingFactory(  # type: ignore[arg-type]
            client,
            manifest,
        ).build(execution)

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].capability, "repository.read")
        result = await bindings[0].handler(
            {"path": "README.md"},
            {"paths": ["README.md"]},
        )

        self.assertEqual(result, "ok")
        self.assertEqual(client.calls[0].context.execution, execution)
        self.assertEqual(client.calls[0].context.scope, {"paths": ["README.md"]})


if __name__ == "__main__":
    unittest.main()
