"""Dual-track ``messages.content`` (str | list[part]) — MM-1 slice tests.

Covers:
* shape validation of the dual-track contract (str passthrough, valid part
  lists, malformed variants rejected loudly);
* fail-closed vision gating against the capability registry (ADR-0105);
* Anthropic native-block conversion from OpenAI-style parts;
* adapter-level payload construction: a parts list reaches the wire untouched
  for a vision model; text-only models get an explicit error BEFORE any
  network I/O.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import app.services.adapter_manager as am  # noqa: E402
from app.services.adapter_manager import (  # noqa: E402
    AnthropicAdapter,
    OpenAICompatibleAdapter,
)
from app.services.message_content import (  # noqa: E402
    ContentShapeError,
    anthropic_blocks_from_content,
    assert_parts_allowed_for_model,
    flatten_text_content,
    has_image_parts,
    validate_dual_track_content,
)
from app.services.tools.multimodal.capability import (  # noqa: E402
    VisionUnsupportedError,
    clear_extra_rules_for_tests,
    register_vision_model,
)


PARTS = [
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
    {"type": "text", "text": "这是什么？"},
]


# ── shape validation ────────────────────────────────────────────────

def test_validate_accepts_str_and_valid_parts() -> None:
    validate_dual_track_content("hello")
    validate_dual_track_content([{"type": "text", "text": "hi"}])
    validate_dual_track_content(PARTS)
    # extra keys are ignored (forward compatibility)
    validate_dual_track_content([{"type": "text", "text": "hi", "extra": 1}])


@pytest.mark.parametrize("bad", [
    None,
    5,
    [None],
    ["plain str item"],
    [{"type": "bogus"}],
    [{"type": "text"}],
    [{"type": "image_url"}],
    [{"type": "image_url", "image_url": "not-a-dict"}],
])
def test_validate_rejects_malformed(bad) -> None:
    with pytest.raises(ContentShapeError):
        validate_dual_track_content(bad)


# ── vision gate ─────────────────────────────────────────────────────

def test_has_image_parts_and_flatten() -> None:
    assert not has_image_parts("just text")
    assert has_image_parts(PARTS)
    assert flatten_text_content("abc") == "abc"
    flat = flatten_text_content(PARTS)
    assert "base64" not in flat          # payload never leaks into text
    assert "[image]" in flat


def test_vision_gate_fail_closed_then_registered_passes() -> None:
    # default rules: no match for a text-only model
    with pytest.raises(VisionUnsupportedError):
        assert_parts_allowed_for_model("kimi", "gpt-3.5-turbo", PARTS)
    # string content never triggers the gate (legacy path unchanged)
    assert_parts_allowed_for_model("kimi", "gpt-3.5-turbo", "plain")

    register_vision_model("kimi", "test-vision-*")
    try:
        assert_parts_allowed_for_model("kimi", "test-vision-1", PARTS)
    finally:
        clear_extra_rules_for_tests()


# ── Anthropic block conversion ──────────────────────────────────────

def test_anthropic_blocks_from_str_wraps_single_text() -> None:
    assert anthropic_blocks_from_content("hello") == [{"type": "text", "text": "hello"}]


def test_anthropic_blocks_convert_data_uri_and_url() -> None:
    blocks = anthropic_blocks_from_content(PARTS)
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"] == {
        "type": "base64", "media_type": "image/png", "data": "aGVsbG8=",
    }
    assert blocks[1] == {"type": "text", "text": "这是什么？"}

    url_blocks = anthropic_blocks_from_content(
        [{"type": "image_url", "image_url": {"url": "https://example.com/x.png"}}]
    )
    assert url_blocks[0]["source"] == {"type": "url", "url": "https://example.com/x.png"}


# ── adapter-level payload construction ──────────────────────────────


class _FakeResp:
    status_code = 200
    text = ""

    def __init__(self, data: dict) -> None:
        self._data = data

    def json(self) -> dict:
        return self._data


_OPENAI_OK = {
    "choices": [{"message": {"role": "assistant", "content": "好的"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
}


def test_openai_execute_passes_parts_through_for_vision_model(monkeypatch) -> None:
    captured: dict = {}

    async def fake_retry(method, url, **kwargs):
        captured["payload"] = kwargs.get("json_body")
        return _FakeResp(dict(_OPENAI_OK))

    monkeypatch.setattr(am, "_retry_request", fake_retry)
    monkeypatch.setattr(am, "ENABLE_REAL_LLM", True)

    adapter = OpenAICompatibleAdapter()
    adapter.provider_name = "kimi"
    out = asyncio.run(adapter.execute_prompt(
        PARTS, "moonshot-v1-8k-vision-preview", api_key="unit-key"))
    assert out == "好的"
    user_msg = captured["payload"]["messages"][-1]
    assert user_msg == {"role": "user", "content": PARTS}   # untouched passthrough
    # prompt-token fallback (if any) must never choke on a list
    assert isinstance(out, str)


def test_openai_execute_rejects_image_before_network(monkeypatch) -> None:
    attempts: list[tuple] = []

    async def must_not_call(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("network I/O must not happen for gated content")

    monkeypatch.setattr(am, "_retry_request", must_not_call)
    monkeypatch.setattr(am, "ENABLE_REAL_LLM", True)

    adapter = OpenAICompatibleAdapter()
    with pytest.raises(VisionUnsupportedError):
        asyncio.run(adapter.execute_prompt(PARTS, "gpt-3.5-turbo", api_key="unit-key"))
    assert not attempts


def test_openai_stream_rejects_before_network(monkeypatch) -> None:
    monkeypatch.setattr(am, "ENABLE_REAL_LLM", True)

    adapter = OpenAICompatibleAdapter()
    from app.services.message_content import ContentShapeError

    # malformed part list → ContentShapeError before anything else
    async def drain_bad_shape() -> None:
        async for _chunk in adapter.stream_prompt(
                [{"type": "bogus"}], "some-model", api_key="unit-key"):
            pass

    with pytest.raises(ContentShapeError):
        asyncio.run(drain_bad_shape())

    # valid parts but text-only model → vision gate before the wire
    async def drain_gated() -> None:
        async for _chunk in adapter.stream_prompt(
                PARTS, "gpt-3.5-turbo", api_key="unit-key"):
            pass

    with pytest.raises(VisionUnsupportedError):
        asyncio.run(drain_gated())


def test_openai_stream_does_not_cut_reasoning_at_fixed_1500_chars(monkeypatch) -> None:
    """Reasoning length is governed by provider tokens, never a char cap."""

    reasoning = "推" * 1601

    class _StreamResponse:
        status_code = 200

        async def aiter_lines(self):
            yield 'data: ' + json.dumps({
                "choices": [{"delta": {"reasoning_content": reasoning}}],
            }, ensure_ascii=False)
            yield 'data: ' + json.dumps({
                "choices": [{"delta": {"content": "最终答案"}}],
            }, ensure_ascii=False)
            yield "data: [DONE]"

        async def aread(self):
            return b""

    class _StreamContext:
        async def __aenter__(self):
            return _StreamResponse()

        async def __aexit__(self, *args):
            return False

    class _Client:
        def stream(self, *args, **kwargs):
            return _StreamContext()

    monkeypatch.setattr(am, "ENABLE_REAL_LLM", True)
    monkeypatch.setattr(am, "_get_client", lambda: _Client())
    adapter = OpenAICompatibleAdapter()

    async def collect() -> list[str]:
        return [chunk async for chunk in adapter.stream_prompt(
            "weather", "deepseek-v4-flash", api_key="unit-key"
        )]

    chunks = asyncio.run(collect())
    rendered = "".join(chunks)
    assert reasoning in rendered
    assert "最终答案" in rendered
    assert "思考已达到上限" not in rendered


def test_anthropic_execute_converts_blocks_for_registered_vision(monkeypatch) -> None:
    captured: dict = {}

    async def fake_retry(method, url, **kwargs):
        captured["payload"] = kwargs.get("json_body")
        return _FakeResp({
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 7, "output_tokens": 2},
        })

    monkeypatch.setattr(am, "_retry_request", fake_retry)
    monkeypatch.setattr(am, "ENABLE_REAL_LLM", True)
    register_vision_model("anthropic", "claude-vision-test")
    try:
        adapter = AnthropicAdapter()
        out = asyncio.run(adapter.execute_prompt(
            PARTS, "claude-vision-test", api_key="unit-key"))
    finally:
        clear_extra_rules_for_tests()
    assert out == "ok"
    user_msg = captured["payload"]["messages"][0]
    assert user_msg["content"][0]["type"] == "image"
    assert user_msg["content"][0]["source"]["type"] == "base64"


def test_mock_branch_tolerates_list_input(monkeypatch) -> None:
    monkeypatch.setattr(am, "ENABLE_REAL_LLM", False)
    adapter = OpenAICompatibleAdapter()
    out = asyncio.run(adapter.execute_prompt(
        [{"type": "text", "text": "hi there"}], "mock-model"))
    assert isinstance(out, str) and out
