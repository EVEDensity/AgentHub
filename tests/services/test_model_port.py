from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from app.services.harness_service import FunctionResult, HarnessRequest
from app.services.model_port import ModelAdapterPort, normalize_model_response


class FakeAdapter:
    def __init__(self, response: str, *, usage: object = None) -> None:
        self.response = response
        self.last_usage = usage
        self.calls: list[dict[str, Any]] = []

    async def execute_prompt(self, prompt: str, model: str, *args: Any, **kwargs: Any) -> str:
        self.calls.append({"prompt": prompt, "model": model, "args": args, "kwargs": kwargs})
        return self.response


class ModelPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_forwards_request_and_renders_tool_results(self) -> None:
        adapter = FakeAdapter(
            '{"content":"done"}',
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        port = ModelAdapterPort(
            adapter,
            model="test-model",
            system_prompt="system",
            tools=[{"type": "function", "function": {"name": "lookup"}}],
            prompt_token_cost=0.01,
            completion_token_cost=0.02,
        )
        response = await port.complete(
            HarnessRequest(code="answer", language="text", timeout=5, cwd=Path("repo")),
            (
                FunctionResult(
                    call_id="call-1",
                    name="lookup",
                    success=True,
                    content="value",
                ),
            ),
        )

        self.assertEqual(response.content, "done")
        self.assertEqual(response.usage.prompt_tokens, 10)
        self.assertEqual(response.usage.completion_tokens, 5)
        self.assertEqual(response.usage.cost, 0.2)
        call = adapter.calls[0]
        self.assertIn("Tool results:", call["prompt"])
        self.assertIn('"callId": "call-1"', call["prompt"])
        self.assertEqual(call["model"], "test-model")
        self.assertEqual(call["kwargs"]["system_prompt"], "system")

    def test_normalize_internal_and_openai_tool_calls(self) -> None:
        internal = normalize_model_response(
            '{"tool_calls":[{"id":"a","name":"lookup","arguments":{"q":"x"}}]}'
        )
        openai = normalize_model_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "b",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"q":"y"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )

        self.assertEqual(internal.tool_calls[0].arguments, {"q": "x"})
        self.assertEqual(openai.tool_calls[0].id, "b")
        self.assertEqual(openai.tool_calls[0].arguments, {"q": "y"})

    def test_normalize_malformed_arguments_as_validation_feedback(self) -> None:
        response = normalize_model_response(
            '{"tool_calls":[{"name":"lookup","arguments":"not-json"}]}'
        )
        self.assertEqual(response.tool_calls[0].arguments, {"__raw_arguments__": "not-json"})

    def test_plain_text_is_preserved(self) -> None:
        self.assertEqual(normalize_model_response("plain answer").content, "plain answer")

    def test_unknown_json_shape_is_preserved_as_text(self) -> None:
        self.assertEqual(
            normalize_model_response({"answer": "plain JSON"}).content,
            '{"answer": "plain JSON"}',
        )

    async def test_missing_or_invalid_usage_defaults_to_zero(self) -> None:
        for usage in (None, {"prompt_tokens": -1, "completion_tokens": "5"}, []):
            with self.subTest(usage=usage):
                response = await ModelAdapterPort(
                    FakeAdapter('{"content":"done"}', usage=usage),
                    model="test-model",
                ).complete(HarnessRequest(code="x", language="text", timeout=1), ())
                self.assertEqual(response.usage.prompt_tokens, 0)
                self.assertEqual(response.usage.completion_tokens, 0)
                self.assertEqual(response.usage.cost, 0)

    def test_negative_token_cost_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative"):
            ModelAdapterPort(FakeAdapter("ok"), model="test", prompt_token_cost=-0.1)
        with self.assertRaisesRegex(ValueError, "non-negative"):
            ModelAdapterPort(
                FakeAdapter("ok"),
                model="test",
                completion_token_cost=float("nan"),
            )
