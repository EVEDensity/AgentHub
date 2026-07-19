from __future__ import annotations

import asyncio

from app.services.memory.models import (
    CognitiveMemoryType,
    MemoryDocument,
    MemoryMeta,
    MemoryScope,
    MemoryType,
)
from app.services.memory.session_store import SessionMemoryStore
from app.services.memory.storage import MemoryStorage


def test_legacy_frontmatter_gets_safe_cognitive_defaults() -> None:
    document = MemoryDocument.parse(
        "---\nname: preference\ndescription: reply style\ntype: user\n---\n\nconcise"
    )
    assert document.meta.memory_type == CognitiveMemoryType.SEMANTIC
    assert document.meta.scope == MemoryScope.USER
    assert document.meta.source == "legacy-file"
    assert document.meta.version == 1


def test_cognitive_metadata_round_trip() -> None:
    meta = MemoryMeta(
        name="deploy-sop",
        description="production release procedure",
        type=MemoryType.PROJECT,
        memory_type=CognitiveMemoryType.PROCEDURAL,
        scope=MemoryScope.TEAM,
        source="workflow:42",
        version=3,
    )
    parsed = MemoryDocument.parse(MemoryDocument(meta, "steps").to_markdown())
    assert parsed.meta == meta


def test_storage_preserves_metadata_and_increments_version(tmp_path) -> None:
    async def run() -> None:
        storage = MemoryStorage(tmp_path / "memory")
        first = await storage.save(
            "preference", "reply style", MemoryType.USER, "concise",
            memory_type=CognitiveMemoryType.SEMANTIC,
            scope=MemoryScope.USER,
            source="manual",
        )
        second = await storage.save(
            "preference", "reply style", MemoryType.USER, "very concise",
            filename=first.file_path.split("\\")[-1],
        )
        assert second.meta.memory_type == CognitiveMemoryType.SEMANTIC
        assert second.meta.version == 2

    asyncio.run(run())


def test_session_store_classifies_conversation_as_episodic(tmp_path) -> None:
    async def run() -> None:
        store = SessionMemoryStore(tmp_path / "user")
        await store.append_turn("s1", "question", "answer")
        info = await store.get_session_info("s1")
        assert info is not None
        assert info.memory_type == CognitiveMemoryType.EPISODIC.value
        assert info.scope == MemoryScope.SESSION.value
        assert info.source == "conversation"
        assert info.version == 2

    asyncio.run(run())
