from __future__ import annotations

import asyncio

from app.services.memory.semantic_memory import (
    SemanticCandidate,
    SemanticMemoryStore,
    extract_semantic_candidates,
)


def test_extract_semantic_candidates_requires_explicit_signal() -> None:
    candidates = extract_semantic_candidates(
        "用户询问了部署问题。用户偏好：回答保持简洁。约束：不得自动发布生产环境。"
    )
    assert [candidate.category for candidate in candidates] == ["preference", "constraint"]
    assert all(candidate.confidence >= 0.7 for candidate in candidates)


def test_semantic_store_supersedes_conflicting_value(tmp_path) -> None:
    async def run() -> None:
        store = SemanticMemoryStore(tmp_path / "user")
        await store.upsert_candidates(
            [SemanticCandidate("preference:reply-style", "concise", "preference", 0.8)],
            source="session-summary",
            source_session_id="s1",
            source_event_id="e1",
        )
        changed = await store.upsert_candidates(
            [SemanticCandidate("preference:reply-style", "detailed", "preference", 0.85)],
            source="session-summary",
            source_session_id="s2",
            source_event_id="e2",
        )
        all_records = await store.list_records(active_only=False)
        active = await store.list_records(active_only=True)
        assert len(all_records) == 2
        assert len(active) == 1
        assert active[0].value == "detailed"
        assert active[0].version == 2
        assert changed[0].source_session_id == "s2"
        old_record = next(record for record in all_records if record.status == "superseded")
        assert old_record.superseded_by == active[0].id

    asyncio.run(run())


def test_semantic_search_includes_preferences_and_relevant_facts(tmp_path) -> None:
    async def run() -> None:
        store = SemanticMemoryStore(tmp_path / "user")
        await store.upsert_candidates(
            [
                SemanticCandidate("preference:language", "使用中文回复", "preference", 0.9),
                SemanticCandidate("fact:deploy", "生产环境运行在 Kubernetes", "fact", 0.85),
            ],
            source="summary",
            source_session_id="s1",
            source_event_id="e1",
        )
        results = await store.search("如何部署 Kubernetes")
        assert any(record.category == "fact" for record in results)
        assert any(record.category == "preference" for record in results)

    asyncio.run(run())
