from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.services.memory.l2_vector import (
    EmbeddingVersion,
    L2VectorEntry,
    L2VectorIndex,
    LocalHashEmbedder,
)
from app.services.memory.semantic_memory import (
    SemanticCandidate,
    SemanticMemoryRecord,
    SemanticMemoryStore,
)
from app.utils.async_file import awrite_json


def test_embedder_is_deterministic_and_normalized() -> None:
    embedder = LocalHashEmbedder(dim=128)
    first = embedder.embed("使用中文回复 保持简洁")
    second = embedder.embed("使用中文回复 保持简洁")
    assert first == second
    norm = sum(value * value for value in first) ** 0.5
    assert abs(norm - 1.0) < 1e-6
    assert len(embedder.embed("")) == 128


def test_vector_search_filters_scope_session_and_tombstone(tmp_path) -> None:
    async def run() -> None:
        index = L2VectorIndex(tmp_path)
        embedder = LocalHashEmbedder()
        version = EmbeddingVersion.current().tag
        now = datetime.now(UTC).isoformat()
        await index.upsert(L2VectorEntry(
            record_id="r1", text="商品缓存策略", scope="user", session_id="s1",
            embedding_version=version, vector=embedder.embed("商品缓存策略"),
            created_at=now, updated_at=now,
        ))
        await index.upsert(L2VectorEntry(
            record_id="r2", text="数据库索引", scope="user", session_id="s2",
            embedding_version=version, vector=embedder.embed("数据库索引"),
            created_at=now, updated_at=now,
        ))
        await index.upsert(L2VectorEntry(
            record_id="r3", text="商品缓存策略", scope="team", session_id="s3",
            embedding_version=version, vector=embedder.embed("商品缓存策略"),
            created_at=now, updated_at=now,
        ))
        # tenant/session filter: only the user-scoped r1 is a hit for s1
        hits = await index.search(embedder.embed("商品缓存策略"), scope="user", session_ids=["s1"], limit=3)
        assert [hit.record_id for _score, hit in hits] == ["r1"]

        # session filter excludes other sessions regardless of scope
        hits = await index.search(embedder.embed("商品缓存策略"), session_ids=["s2"], limit=3)
        assert all(hit.session_id == "s2" for _score, hit in hits)

        # tombstone excludes r1 from result
        await index.tombstone_by_source(["s1"])
        await index.prune()
        hits = await index.search(embedder.embed("商品缓存策略"), scope="user", session_ids=["s1"], limit=3)
        assert hits == []

    asyncio.run(run())


def test_embedding_version_change_marks_vectors_stale(tmp_path) -> None:
    async def run() -> None:
        index = L2VectorIndex(tmp_path)
        embedder = LocalHashEmbedder()
        now = datetime.now(UTC).isoformat()
        await index.upsert(L2VectorEntry(
            record_id="r1", text="旧版本向量", scope="user", session_id="s1",
            embedding_version="local-hash-v1:old", vector=embedder.embed("旧版本向量"),
            created_at=now, updated_at=now,
        ))
        current = EmbeddingVersion.current().tag
        assert await index.stale_ids(current) == ["r1"]
        metrics = await index.metrics()
        assert metrics["stale"] == 1

    asyncio.run(run())


def test_delete_source_propagates_to_records_and_vectors(tmp_path) -> None:
    async def run() -> None:
        store = SemanticMemoryStore(tmp_path, embedder=LocalHashEmbedder())
        await store.upsert_candidates(
            [SemanticCandidate("preference:reply-style", "concise", "preference", 0.8)],
            source="summary", source_session_id="s1", source_event_id="e1",
        )
        await store.upsert_candidates(
            [SemanticCandidate("fact:stack", "生产环境运行在 Kubernetes", "fact", 0.85)],
            source="summary", source_session_id="s2", source_event_id="e2",
        )
        result = await store.delete_source("s1")
        assert result["records_deleted"] == 1
        assert result["vectors_tombstoned"] == 1
        assert result["vectors_purged"] == 1  # tombstone swept immediately
        active = await store.list_records(active_only=True)
        assert [record.key for record in active] == ["fact:stack"]
        metrics = await store._l2.metrics()
        assert metrics["tombstoned"] == 0
        assert metrics["active"] == 1

    asyncio.run(run())


def test_retention_expires_records_and_purges_vectors(tmp_path) -> None:
    async def run() -> None:
        store = SemanticMemoryStore(tmp_path, embedder=LocalHashEmbedder())
        await store.upsert_candidates(
            [
                SemanticCandidate("preference:language", "使用中文", "preference", 0.9),
                SemanticCandidate("fact:deploy", "部署在 Kubernetes", "fact", 0.85),
            ],
            source="summary", source_session_id="s1", source_event_id="e1",
        )
        # Manually set a past expiry on one active record.
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        async with store._lock:
            raw = await store._read_records()
            records = [SemanticMemoryRecord(**item) for item in raw]
            for record in records:
                if record.category == "preference":
                    record.expires_at = past
            await awrite_json(store._path, [record.__dict__ for record in records])

        result = await store.prune_expired()
        assert result["records_expired"] == 1
        assert result["vectors_purged"] == 1
        active = await store.list_records(active_only=True)
        assert [record.category for record in active] == ["fact"]
        metrics = await store._l2.metrics()
        assert metrics["active"] == 1

    asyncio.run(run())


def test_reindex_after_embedding_version_change(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        store = SemanticMemoryStore(tmp_path, embedder=LocalHashEmbedder())
        await store.upsert_candidates(
            [SemanticCandidate("preference:reply-style", "concise", "preference", 0.8)],
            source="summary", source_session_id="s1", source_event_id="e1",
        )
        record = (await store.list_records(active_only=True))[0]
        assert record.embedding_version == EmbeddingVersion.current().tag
        assert await store.reindex() == 0  # nothing stale

        monkeypatch.setenv("AGENTHUB_EMBEDDING_MODEL", "local-hash-v2")
        assert await store.reindex() == 1  # version changed -> re-embedded
        record = (await store.list_records(active_only=True))[0]
        assert record.embedding_version == EmbeddingVersion.current().tag
        assert "local-hash-v2" in record.embedding_version
        assert await store.reindex() == 0

    asyncio.run(run())
    monkeypatch.delenv("AGENTHUB_EMBEDDING_MODEL", raising=False)