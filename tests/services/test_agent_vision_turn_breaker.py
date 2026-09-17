"""Unit tests for the two R4-4 extraction modules.

* :mod:`app.services.agent.circuit_breaker` — three-tier tool-loop breaker
  (per-tool same-error streak / missing-params streak / all-failed catch-all)
* :mod:`app.services.agent.vision_turn` — ``decide_turn_vision`` turn
  decision (vision parts vs text-only describe fallback vs billing parity
  with what actually rides the wire).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services.agent.circuit_breaker import ToolLoopCircuitBreaker  # noqa: E402
from app.services.agent.vision_turn import (  # noqa: E402
    decide_turn_vision,
    extract_screenshot_uris,
)


B64 = "aGVsbG8="
URI = f"data:image/png;base64,{B64}"


def _fail(tool: str, error: str) -> dict:
    return {"tool_name": tool, "success": False, "error": error}


def _ok(tool: str) -> dict:
    return {"tool_name": tool, "success": True}


# ── circuit_breaker ──────────────────────────────────────────────────

def test_tier1_trips_after_three_same_error_rounds() -> None:
    breaker = ToolLoopCircuitBreaker()
    assert breaker.assess([_fail("a", "boom")], 0) is None
    assert breaker.assess([_fail("a", "boom")], 1) is None
    event = breaker.assess([_fail("a", "boom")], 2)
    assert event is not None and event.tier == "tier1"
    assert event.tools[0]["tool_name"] == "a" and event.tools[0]["rounds"] == 3


def test_tier1_error_change_restarts_streak() -> None:
    breaker = ToolLoopCircuitBreaker()
    # companion success keeps all_failed False so tier3 never interferes
    for i in range(2):
        breaker.assess([_fail("a", "boom"), _ok("b")], i)
    # different fingerprint → streak restarts at 1, no trip
    breaker.assess([_fail("a", "different"), _ok("b")], 2)
    assert breaker._failure_history["a"]["consecutive_rounds"] == 1


def test_success_resets_tool_history_and_round_counters() -> None:
    breaker = ToolLoopCircuitBreaker()
    breaker.assess([_fail("a", "e")], 0)
    breaker.assess([_fail("a", "e")], 1)
    # success clears per-tool history and round-level counters
    assert breaker.assess([_ok("a")], 2) is None
    assert "a" not in breaker._failure_history
    assert breaker._missing_param_rounds == 0


def test_tier2_missing_params_two_rounds_trip() -> None:
    breaker = ToolLoopCircuitBreaker()
    miss = {"tool_name": "t", "success": False, "missing_params": ["q"]}
    assert breaker.assess([miss], 0) is None
    event = breaker.assess([miss], 1)
    assert event is not None and event.tier == "tier2"


def test_tier3_all_failed_three_rounds_trip_and_reset_on_partial() -> None:
    breaker = ToolLoopCircuitBreaker()
    # all-fail rounds with per-round distinct tools/errors: tier1 stays
    # silent (fresh fingerprint each time), tier3 trips on the third round
    assert breaker.assess([_fail("x", "net down")], 0) is None
    assert breaker.assess([_fail("y", "timeout now")], 1) is None
    event = breaker.assess([_fail("z", "dead again")], 2)
    assert event is not None and event.tier == "tier3"

    fresh = ToolLoopCircuitBreaker()
    for i in range(3):
        # partial success every round keeps round-level tiers disarmed
        assert fresh.assess([_fail(f"p{i}", f"r{i}"), _ok("q")], i) is None


# ── vision_turn.decide_turn_vision ───────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def test_decide_queued_screenshots_become_parts_for_vision_model() -> None:
    queue = [URI]
    extra, note, billed = _run(decide_turn_vision(
        queued_vision=queue, image_parts=[], iteration=1,
        provider="kimi", model_name="moonshot-v1-8k-vision-preview"))
    assert len(extra) == 1 and extra[0]["type"] == "image_url"
    assert note == "" and billed == 1
    assert queue == []                      # consumed
    # payload passes through untouched
    assert extra[0]["image_url"]["url"] == URI


def test_decide_text_only_model_gets_describe_note(monkeypatch) -> None:
    import app.services.agent.vision_turn as vt

    async def fake_describe(uri: str) -> str:
        return "登录页截图，含蓝色按钮"

    monkeypatch.setattr(vt, "describe_screenshot_fallback", fake_describe)
    queue = [URI, URI + "-second"]
    extra, note, billed = _run(decide_turn_vision(
        queued_vision=queue, image_parts=[], iteration=1,
        provider="mock", model_name="mock"))
    assert extra == [] and billed == 0
    assert "【上一轮浏览器截图的视觉描述】" in note and "登录页" in note
    assert len(queue) == 1                  # one degrade per turn, rest stay queued


def test_decide_attachment_parts_only_first_iteration() -> None:
    parts = [{"type": "image_url", "image_url": {"url": URI}}]
    _, _, billed0 = _run(decide_turn_vision(
        queued_vision=[], image_parts=parts, iteration=0,
        provider="kimi", model_name="moonshot-v1-8k-vision-preview"))
    _, _, billed_later = _run(decide_turn_vision(
        queued_vision=[], image_parts=parts, iteration=2,
        provider="kimi", model_name="moonshot-v1-8k-vision-preview"))
    assert billed0 == 1                     # billed to fit_prompt on turn 0
    assert billed_later == 0                # later turns never re-bill attachments


def test_decide_empty_paths_are_noops() -> None:
    out = _run(decide_turn_vision(
        queued_vision=[], image_parts=[], iteration=0,
        provider="openai", model_name="gpt-4o"))
    assert out == ([], "", 0)


def test_extract_screenshot_uris_passthrough_non_screenshot() -> None:
    plain = {"tool_name": "web_search", "success": True, "result": {"hits": 3}}
    uris, sanitized = extract_screenshot_uris([plain])
    assert uris == []
    assert sanitized[0] is plain            # same object, untouched
