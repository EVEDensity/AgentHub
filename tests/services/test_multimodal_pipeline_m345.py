"""MM-3 / MM-4 / MM-5 pipeline slice tests.

* MM-3 — fit_prompt image billing + compaction placeholder semantics
* MM-4 — screenshot payload hijack for next-turn vision refeed (+degrade)
* MM-5 — guardrail image hygiene (fail-closed) and multimodal content scan
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services.context_compaction import compact_content_parts  # noqa: E402
from app.services.guardrails import scan_image_source, scan_multimodal_content  # noqa: E402
from app.services.agent.vision_turn import (  # noqa: E402
    describe_screenshot_fallback,
    extract_screenshot_uris,
)
from app.services.token_budget import fit_prompt  # noqa: E402
from app.services.tools.multimodal.content_parts import IMAGE_TOKEN_COST  # noqa: E402


B64 = "aGVsbG8="  # tiny valid-ish base64 body
PNG_URI = f"data:image/png;base64,{B64}"


# ── MM-3: fit_prompt image branch ─────────────────────────────────────

def test_fit_prompt_bills_images_and_reports_items() -> None:
    prompt = "符号消息:" + "x" * 200
    _, base = fit_prompt(prompt, "openai", "gpt-4o", anchor="符号消息:")
    _, billed = fit_prompt(prompt, "openai", "gpt-4o", anchor="符号消息:", image_count=3)
    assert base["images"] == 0 and base["image_tokens"] == 0
    assert billed["image_tokens"] == 3 * IMAGE_TOKEN_COST
    assert billed["images"] == 3
    assert billed["tokens_before"] - base["tokens_before"] == 3 * IMAGE_TOKEN_COST


def test_fit_prompt_over_limit_reserves_room_for_images() -> None:
    long_prompt = "符号消息:" + ("中文内容用于膨胀长度。" * 4000)
    _text, out = fit_prompt(
        long_prompt, "openai", "gpt-4o",
        anchor="符号消息:", image_count=4, output_reserve=8_000_000,
    )
    assert out["truncated"] is True
    assert out["image_tokens"] == 4 * IMAGE_TOKEN_COST
    # text portion must have been cut so text+images fits an implied limit:
    # after == text_after + image_tokens and text shrank massively
    assert out["tokens_after"] < out["tokens_before"]
    assert out["tokens_after"] <= 4 * IMAGE_TOKEN_COST + 900_000


def test_compact_content_parts_replaces_images_with_placeholder() -> None:
    parts = [
        {"type": "image_url", "image_url": {"url": PNG_URI}},
        {"type": "text", "text": "这个需求需要  " + "很长的描述内容，" * 80},
        {"type": "text", "text": ""},
        {"type": "unknown", "junk": True},
    ]
    out = compact_content_parts(parts, max_chars=50)
    assert out[0] == {"type": "text", "text": "[用户曾发送图片]"}
    joined = " ".join(p["text"] for p in out)
    assert B64 not in joined and PNG_URI not in joined   # payloads never leak
    assert len(out[1]["text"]) <= 60                     # compacted
    assert not any(p.get("type") != "text" for p in out)


# ── MM-4: screenshot hijack ───────────────────────────────────────────

def _shot_result(b64: str) -> dict:
    return {
        "tool_name": "browser_screenshot",
        "success": True,
        "result": {"screenshot_base64": b64, "url": "https://x"},
    }


def test_extract_screenshot_uris_strips_payload() -> None:
    uris, clean = extract_screenshot_uris([_shot_result(B64)])
    assert uris == [PNG_URI]
    r = clean[0]["result"]
    assert r["screenshot_base64"] == ""
    assert "vision_note" in r
    # normal tool results pass through untouched
    plain = {"tool_name": "web_search", "success": True, "result": "hi"}
    _, clean2 = extract_screenshot_uris([plain])
    assert clean2[0] is plain


def test_extract_screenshot_uris_caps_at_turn_limit() -> None:
    many = [_shot_result(f"{i}:{B64}") for i in range(9)]
    uris, _clean = extract_screenshot_uris(many)
    assert len(uris) == 4   # MAX_IMAGES_PER_TURN


def test_describe_fallback_degrades_gracefully(monkeypatch) -> None:
    import app.services.tools.multimodal.tools as mm_tools

    async def boom(**kwargs):
        raise RuntimeError("no sub-model configured")

    monkeypatch.setattr(mm_tools, "image_describe_handler", boom)
    note = asyncio.run(describe_screenshot_fallback(PNG_URI))
    assert isinstance(note, str) and note                      # never raises
    assert "失败" in note or "不可用" in note

    async def ok(**kwargs):
        return {"success": True, "result": {"description": "一张登录页截图"}}

    monkeypatch.setattr(mm_tools, "image_describe_handler", ok)
    assert asyncio.run(describe_screenshot_fallback(PNG_URI)) == "一张登录页截图"


# ── MM-5: guardrail hygiene ───────────────────────────────────────────

def test_scan_image_source_accepts_valid_uri_and_remote_url() -> None:
    ok_png = scan_image_source("data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==")
    assert ok_png.passed is True
    ok_url = scan_image_source("https://example.com/a.png")
    assert ok_url.passed is True


def test_scan_image_source_blocks_svg_bad_shape_and_oversize() -> None:
    bad_svg = scan_image_source("data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=")
    assert bad_svg.passed is False

    bad_shape = scan_image_source("data:image/png;base64,@@@")
    assert bad_shape.passed is False

    oversize = "A" * (9 * 1024 * 1024)
    bad_size = scan_image_source(f"data:image/png;base64,{oversize}")
    assert bad_size.passed is False


def test_scan_multimodal_content_tags_failing_slot() -> None:
    content = [
        {"type": "image_url", "image_url": {"url": "https://ok.example/x.png"}},
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="}},
    ]
    result = scan_multimodal_content(content)
    assert result.passed is False
    tagged = [f.rule for f in result.flags if f.severity.value == "block"]
    assert any(rule.startswith("image_hygiene@content[2]") for rule in tagged)

    # plain strings (legacy track) trivially pass
    assert scan_multimodal_content("just text").passed is True
