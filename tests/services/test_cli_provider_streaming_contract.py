from __future__ import annotations

import unittest

from app.services.model_port import ModelAdapterPort
from app.services.harness_service import HarnessRequest


class _FixtureAdapter:
    last_usage = {"prompt_tokens": 3, "completion_tokens": 2}

    async def execute_prompt(self, *args, **kwargs):
        return "fallback"

    async def stream_prompt(self, *args, **kwargs):
        for chunk in ("", "hello", " ", "world"):
            yield chunk


class StreamingContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_forwards_non_empty_chunks_in_order(self) -> None:
        received: list[str] = []
        port = ModelAdapterPort(_FixtureAdapter(), model="fixture")
        response = await port.stream(
            HarnessRequest(code="prompt", language="text", timeout=1, on_text_delta=received.append),
            (),
        )
        self.assertEqual(received, ["hello", " ", "world"])
        self.assertEqual(response.content, "hello world")
        self.assertEqual(response.usage.total_tokens, 5)

