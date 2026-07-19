from __future__ import annotations

from app.services.memory_context import (
    MemoryContextSection,
    build_memory_context,
    deduplicate_text,
)


def test_deduplicate_text_removes_history_overlap() -> None:
    history = "user: deploy service\nassistant: deployment finished"
    candidate = history + "\n\nUnresolved: verify production health"
    result = deduplicate_text(candidate, [history])
    assert "deployment finished" not in result
    assert "verify production health" in result


def test_build_memory_context_prioritizes_session_summary() -> None:
    result, stats = build_memory_context(
        [
            MemoryContextSection("global-summary", "global preference", 3),
            MemoryContextSection("session-summary", "current decision", 1),
        ],
        max_tokens=20,
        provider="unknown",
        model="unknown",
    )
    assert "session-summary" in result
    assert stats["tokens_after"] <= 20
