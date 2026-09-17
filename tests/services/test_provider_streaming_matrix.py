from __future__ import annotations

import asyncio
import pytest

from app.services.model_port import ModelAdapterPort
from app.services.harness_service import HarnessRequest


class _ProviderFixture:
    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}

    async def execute_prompt(self, *args, **kwargs):
        return "ok"

    async def stream_prompt(self, *args, **kwargs):
        yield f"{self.provider}:"
        yield "ok"


@pytest.mark.parametrize("provider", ["deepseek", "openai", "anthropic", "zhipu"])
def test_provider_streaming_matrix(provider: str) -> None:
    async def run() -> None:
        chunks: list[str] = []
        port = ModelAdapterPort(_ProviderFixture(provider), model="v4-flash")
        response = await port.stream(HarnessRequest(code="ping", language="text", timeout=1, on_text_delta=chunks.append), ())
        assert "".join(chunks) == f"{provider}:ok"
        assert response.content == f"{provider}:ok"
    asyncio.run(run())
