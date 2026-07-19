from __future__ import annotations

from app.services.agent_prompt_context import (
    build_attachment_context,
    build_quote_context,
    estimate_token_usage,
    format_conversation_for_prompt,
)


def test_build_attachment_context_truncates_text_and_returns_metadata() -> None:
    content = "x" * 12_500
    text, meta = build_attachment_context([
        {"name": "notes.md", "type": "text/markdown", "size": 12_500, "content": content},
    ])

    assert "notes.md" in text
    assert len(text) < len(content)
    assert meta == [{"name": "notes.md", "type": "text/markdown", "size": 12500}]


def test_build_quote_context_truncates_long_messages() -> None:
    text = build_quote_context([
        {
            "originalSender": "alice",
            "originalTimestamp": "2026-07-18T10:00:00Z",
            "quotedText": "y" * 2100,
            "isFullMessage": True,
        }
    ])

    assert "[用户引用的历史消息]" in text
    assert "alice" in text
    assert "已截断" in text


def test_estimate_token_usage_prefers_cjk_density() -> None:
    ascii_prompt, ascii_completion, ascii_total = estimate_token_usage("abcd", "efgh")
    cjk_prompt, cjk_completion, cjk_total = estimate_token_usage("你好世界", "再见世界")

    assert ascii_total > 0
    assert cjk_prompt >= ascii_prompt
    assert cjk_completion >= ascii_completion
    assert cjk_total >= ascii_total


def test_format_conversation_for_prompt_supports_basic_turns() -> None:
    text = format_conversation_for_prompt([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ])

    assert "hello" in text
    assert "world" in text
