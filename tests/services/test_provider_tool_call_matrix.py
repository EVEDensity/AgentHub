from __future__ import annotations

import asyncio
import json

import pytest

from app.services.model_port import ModelAdapterPort
from app.services.harness_service import HarnessRequest


class _ToolFixture:
    last_usage = {"prompt_tokens": 2, "completion_tokens": 3}

    def __init__(self, payload: dict):
        self.payload = payload

    async def execute_prompt(self, *args, **kwargs):
        return json.dumps(self.payload)


@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("openai", {"choices": [{"message": {"tool_calls": [{"id": "c1", "function": {"name": "file_read", "arguments": '{"path":"README.md"}'}}]}}]}),
        ("anthropic", {"tool_calls": [{"id": "c2", "name": "file_read", "arguments": {"path": "README.md"}}]}),
        ("deepseek", {"tool_calls": [{"id": "c3", "function": {"name": "file_read", "arguments": {"path": "README.md"}}}]}),
    ],
)
def test_provider_tool_call_shapes_normalize(provider: str, payload: dict) -> None:
    async def run() -> None:
        port = ModelAdapterPort(_ToolFixture(payload), model="v4-flash")
        response = await port.complete(HarnessRequest(code="read", language="text", timeout=1), ())
        assert response.tool_calls[0].name == "file_read"
        assert response.tool_calls[0].arguments["path"] == "README.md"
    asyncio.run(run())


def test_openai_compatible_adapter_preserves_tool_call_id_and_rejects_raw_arguments() -> None:
    from app.services.adapter_manager import OpenAICompatibleAdapter

    adapter = OpenAICompatibleAdapter()
    adapter.provider_name = "deepseek"
    raw = json.dumps({"choices": [{"message": {"tool_calls": [{"id": "provider-call-7", "function": {"name": "file_read", "arguments": "{bad"}}]}}]})

    class Fixture(OpenAICompatibleAdapter):
        async def _unused(self):
            return None

    async def run() -> None:
        class Port:
            last_usage = {}
            async def execute_prompt(self, *args, **kwargs):
                return raw
        port = ModelAdapterPort(Port(), model="x")
        response = await port.complete(HarnessRequest(code="read", language="text", timeout=1), ())
        assert response.tool_calls[0].id == "provider-call-7"
        assert "__raw_arguments__" in response.tool_calls[0].arguments
    asyncio.run(run())
