from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from app.services.memory.l2_vector import (
    EmbeddingProvider,
    EmbeddingVersion,
    L2VectorEntry,
    L2VectorIndex,
)
from app.services.memory.models import CognitiveMemoryType, MemoryScope
from app.utils.async_file import aexists, amkdir, aread_json, awrite_json


@dataclass(frozen=True)
class SemanticCandidate:
    key: str
    value: str
    category: str
    confidence: float


@dataclass
class SemanticMemoryRecord:
    id: str
    key: str
    value: str
    category: str
    confidence: float
    source: str
    source_session_id: str
    source_event_id: str
    version: int
    status: str
    created_at: str
    updated_at: str
    memory_type: str = CognitiveMemoryType.SEMANTIC.value
    scope: str = MemoryScope.USER.value
    expires_at: str = ""
    superseded_by: str = ""
    embedding_version: str = ""


_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("preference", re.compile(r"(?:用户|团队)?(?:偏好|习惯|倾向)[:：]?\s*(.+)", re.IGNORECASE)),
    ("decision", re.compile(r"(?:关键)?(?:决定|决策|结论)[:：]?\s*(.+)", re.IGNORECASE)),
    ("constraint", re.compile(r"(?:约束|必须|禁止|不得|需要遵守)[:：]?\s*(.+)", re.IGNORECASE)),
    ("fact", re.compile(r"(?:事实|已确认|确认信息)[:：]?\s*(.+)", re.IGNORECASE)),
)


def extract_semantic_candidates(summary: str) -> list[SemanticCandidate]:
    """Extract only explicit durable-memory signals from an episodic summary."""
    candidates: list[SemanticCandidate] = []
    seen: set[str] = set()
    for sentence in re.split(r"[。！？!?\n]+", summary):
        sentence = re.sub(r"\s+", " ", sentence).strip(" -\t")
        if len(sentence) < 4:
            continue
        for category, pattern in _CATEGORY_PATTERNS:
            match = pattern.search(sentence)
            if not match:
                continue
            value = match.group(1).strip(" ：:")[:500]
            if len(value) < 2:
                continue
            label = sentence[:match.start(1)].strip(" ：:") or category
            normalized_label = re.sub(r"[^\w\u3400-\u9fff]+", "", label.lower())[:48]
            if normalized_label in {"用户偏好", "团队偏好", "偏好", category}:
                normalized_label += ":" + re.sub(r"[^\w\u3400-\u9fff]+", "", value.lower())[:16]
            key = f"{category}:{normalized_label}"
            if key in seen:
                break
            seen.add(key)
            candidates.append(SemanticCandidate(key, value, category, 0.78))
            break
    return candidates[:12]


class SemanticMemoryStore:
    """Structured semantic sidecar that preserves the existing memory storage."""

    _locks: ClassVar[dict[str, asyncio.Lock]] = {}

    def __init__(
        self,
        user_memory_dir: str | Path,
        *,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._dir = Path(user_memory_dir).resolve() / "semantic"
        self._path = self._dir / "records.json"
        self._lock = self._locks.setdefault(str(self._path), asyncio.Lock())
        # L2 vector sidecar is enabled only when an embedder is supplied; the
        # lexical path keeps working unchanged as the degraded default.
        self._embedder = embedder
        self._l2 = L2VectorIndex(self._dir.parent) if embedder is not None else None

    async def list_records(self, *, active_only: bool = True) -> list[SemanticMemoryRecord]:
        raw = await self._read_records()
        records = [SemanticMemoryRecord(**item) for item in raw]
        if active_only:
            records = [record for record in records if record.status == "active"]
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    async def upsert_candidates(
        self,
        candidates: list[SemanticCandidate],
        *,
        source: str,
        source_session_id: str,
        source_event_id: str,
    ) -> list[SemanticMemoryRecord]:
        if not candidates:
            return []
        async with self._lock:
            raw = await self._read_records()
            records = [SemanticMemoryRecord(**item) for item in raw]
            changed: list[SemanticMemoryRecord] = []
            now = datetime.now(UTC).isoformat()

            for candidate in candidates:
                current = next(
                    (record for record in records if record.key == candidate.key and record.status == "active"),
                    None,
                )
                if current and _normalize(current.value) == _normalize(candidate.value):
                    current.confidence = max(current.confidence, candidate.confidence)
                    current.updated_at = now
                    current.version += 1
                    current.source = source
                    current.source_session_id = source_session_id
                    current.source_event_id = source_event_id
                    changed.append(current)
                    continue

                version = (current.version + 1) if current else 1
                record_id = hashlib.sha256(
                    f"{candidate.key}|{candidate.value}|{version}".encode()
                ).hexdigest()[:24]
                new_record = SemanticMemoryRecord(
                    id=record_id,
                    key=candidate.key,
                    value=candidate.value,
                    category=candidate.category,
                    confidence=candidate.confidence,
                    source=source,
                    source_session_id=source_session_id,
                    source_event_id=source_event_id,
                    version=version,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                if current:
                    current.status = "superseded"
                    current.superseded_by = record_id
                    current.updated_at = now
                records.append(new_record)
                changed.append(new_record)

            await amkdir(self._dir)
            await awrite_json(self._path, [asdict(record) for record in records])
        await self._index_records(changed)
        return changed

    async def search(self, query: str, limit: int = 6) -> list[SemanticMemoryRecord]:
        records = await self.list_records(active_only=True)
        query_terms = _terms(query)
        scored: list[tuple[float, SemanticMemoryRecord]] = []
        for record in records:
            record_terms = _terms(record.key + " " + record.value)
            overlap = len(query_terms & record_terms)
            relevance = overlap / max(1, len(query_terms)) if query_terms else 0.0
            if relevance > 0 or record.category == "preference":
                scored.append((relevance + record.confidence * 0.2, record))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        lexical = [record for _, record in scored]

        # L2 fusion: vector hits rank first, lexical results fill the rest so
        # enabling the vector sidecar never drops existing recall.
        if self._l2 is not None and self._embedder is not None and query_terms:
            vector_hits = await self._l2.search(
                self._embedder.embed(query),
                scope=MemoryScope.USER.value,
                limit=limit,
            )
            by_id: dict[str, SemanticMemoryRecord] = {record.id: record for record in records}
            fused: list[SemanticMemoryRecord] = []
            seen: set[str] = set()
            for _score, hit in vector_hits:
                record = by_id.get(hit.record_id)
                if record is not None:
                    fused.append(record)
                    seen.add(record.id)
            fused.extend(record for record in lexical if record.id not in seen)
            return fused[:limit]
        return lexical[:limit]

    async def _read_records(self) -> list[dict[str, Any]]:
        if not await aexists(self._path):
            return []
        try:
            data = await aread_json(self._path)
            return data if isinstance(data, list) else []
        except (OSError, ValueError, TypeError):
            return []

    # ── L2 vector lifecycle ──────────────────────────────────────────────

    async def _embed_text(self, record: SemanticMemoryRecord) -> str:
        return f"{record.key} {record.value} {record.category}"

    async def _index_records(self, records: list[SemanticMemoryRecord]) -> None:
        """Embed changed active records into the L2 index (best effort)."""
        if self._l2 is None or self._embedder is None:
            return
        version = EmbeddingVersion.current()
        for record in records:
            if record.status != "active":
                continue
            entry = L2VectorEntry(
                record_id=record.id,
                text=await self._embed_text(record),
                scope=record.scope,
                session_id=record.source_session_id,
                embedding_version=version.tag,
                vector=self._embedder.embed(await self._embed_text(record)),
                created_at=record.created_at,
                updated_at=record.updated_at,
                expires_at=record.expires_at,
            )
            await self._l2.upsert(entry)
            record.embedding_version = version.tag
        marked = [record for record in records if record.status == "active" and record.embedding_version]
        if marked:
            async with self._lock:
                raw = await self._read_records()
                updated = [asdict(record) for record in ([SemanticMemoryRecord(**item) for item in raw])]
                version_by_id = {record.id: record.embedding_version for record in marked}
                for item in updated:
                    if item["id"] in version_by_id:
                        item["embedding_version"] = version_by_id[item["id"]]
                await awrite_json(self._path, updated)

    async def delete_source(self, source_session_id: str) -> dict[str, int]:
        """Delete-propagation: tombstone records and vectors of a deleted session."""
        if not source_session_id:
            return {"records_deleted": 0, "vectors_tombstoned": 0, "vectors_purged": 0}
        async with self._lock:
            raw = await self._read_records()
            records = [SemanticMemoryRecord(**item) for item in raw]
            now = datetime.now(UTC).isoformat()
            deleted = 0
            for record in records:
                if record.source_session_id == source_session_id and record.status == "active":
                    record.status = "deleted"
                    record.updated_at = now
                    deleted += 1
            if deleted:
                await awrite_json(self._path, [asdict(record) for record in records])
        tombstoned = await self._l2.tombstone_by_source([source_session_id]) if self._l2 else 0
        purged = (await self._l2.prune())["purged"] if self._l2 else 0
        return {
            "records_deleted": deleted,
            "vectors_tombstoned": tombstoned,
            "vectors_purged": purged,
        }

    async def prune_expired(self) -> dict[str, int]:
        """Retention sweep: expire records past ``expires_at`` and purge vectors."""
        now = datetime.now(UTC).isoformat()
        async with self._lock:
            raw = await self._read_records()
            records = [SemanticMemoryRecord(**item) for item in raw]
            expired: list[str] = []
            for record in records:
                if (
                    record.status == "active"
                    and record.expires_at
                    and record.expires_at <= now
                ):
                    record.status = "expired"
                    record.updated_at = now
                    expired.append(record.id)
            if expired:
                await awrite_json(self._path, [asdict(record) for record in records])
        purged = await self._l2.delete(expired) if self._l2 else 0
        return {"records_expired": len(expired), "vectors_purged": purged}

    async def reindex(self, *, force: bool = False) -> int:
        """Re-embed active records whose vector is missing or stale.

        Returns the number of records reindexed. Called automatically when the
        embedding version changes (``AGENTHUB_EMBEDDING_MODEL`` bump) or on
        demand with ``force=True``.
        """
        if self._l2 is None or self._embedder is None:
            return 0
        records = await self.list_records(active_only=True)
        version = EmbeddingVersion.current()
        stale_ids = set(await self._l2.stale_ids(version.tag))
        candidates = [
            record
            for record in records
            if force
            or not record.embedding_version
            or record.embedding_version != version.tag
            or record.id in stale_ids
        ]
        await self._index_records(candidates)
        return len(candidates)


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", text).lower()


def _terms(text: str) -> set[str]:
    normalized = _normalize(text)
    latin = set(re.findall(r"[a-z0-9_]{2,}", text.lower()))
    cjk = {normalized[index:index + 2] for index in range(max(0, len(normalized) - 1))}
    return latin | cjk
