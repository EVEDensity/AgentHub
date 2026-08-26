"""L2 retrieval-memory vector lifecycle.

Implements the durable-memory gap described in
`docs/architecture/components/memory.md` (section 5, gap 2): a unified
embedding version, retention policy, provenance model, and deletion
propagation across the vector store.

Design notes
------------
* The index is file-backed JSON per user memory directory (``l2/vectors.json``)
  and mirrors the async, lock-guarded file utilities used by the semantic
  store, so it works identically in tests, single-process desktop mode, and
  multi-worker deployments behind a shared volume.
* Retrieval is tenant-scoped: entries carry ``scope`` and ``session_id`` and
  search applies both filters by default to prevent cross-tenant leakage.
* The embedder is pluggable via the :class:`EmbeddingProvider` protocol. The
  bundled :class:`LocalHashEmbedder` is deterministic and dependency-free —
  suitable for offline evaluation and the default local lifecycle (versioning,
  retention, deletion). Swap in a model-based provider when a remote embedding
  endpoint is configured.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Protocol

from app.utils.async_file import aexists, amkdir, aread_json, awrite_json

_DEFAULT_EMBEDDING_MODEL = "local-hash-v1"


class EmbeddingProvider(Protocol):
    """Anything that maps text to a fixed-dimension vector."""

    def embed(self, text: str) -> list[float]: ...


class LocalHashEmbedder:
    """Deterministic, dependency-free hashing embedder (default dim=256).

    Character- and word-ngrams are hashed into a fixed bag-of-buckets vector
    that is L2-normalized. It preserves lexical overlap for CJK and latin
    text, which is enough for the lifecycle mechanics, the offline eval set,
    and recall gates; it is not a semantic model.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError("embedding dim must be positive")
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = re.findall(r"[\u3400-\u9fff]|[a-z0-9_]+", (text or "").lower())
        if not tokens:
            return vector
        grams: set[str] = set(tokens)
        for index in range(max(0, len(tokens) - 1)):
            grams.add(tokens[index] + tokens[index + 1])
        for gram in grams:
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dim
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector


@dataclass(frozen=True)
class EmbeddingVersion:
    """Version identity of the active embedding model.

    ``digest`` is derived from the model name plus a component salt, so any
    change to ``AGENTHUB_EMBEDDING_MODEL`` (or this crate version) yields a
    new tag and marks stored vectors stale for reindexing.
    """

    model: str
    digest: str

    @classmethod
    def current(cls) -> EmbeddingVersion:
        model = _current_embedding_model()
        digest = hashlib.sha256(f"{model}|agenthub-l2-v1".encode()).hexdigest()[:12]
        return cls(model=model, digest=digest)

    @property
    def tag(self) -> str:
        return f"{self.model}:{self.digest}"


def _current_embedding_model() -> str:
    import os

    return os.getenv("AGENTHUB_EMBEDDING_MODEL", _DEFAULT_EMBEDDING_MODEL).strip() or _DEFAULT_EMBEDDING_MODEL


@dataclass
class L2VectorEntry:
    """One indexed memory record with full lifecycle provenance."""

    record_id: str
    text: str
    scope: str
    session_id: str
    embedding_version: str
    vector: list[float]
    created_at: str
    updated_at: str
    expires_at: str = ""
    tombstone: bool = False


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso_leq(value: str, now: str) -> bool:
    """Lexicographic ISO-8601 compare: ``value`` <= ``now`` (UTC timestamps)."""
    return bool(value) and value <= now


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    for left, right in zip(a, b):
        dot += left * right
    return max(0.0, min(1.0, dot))


class L2VectorIndex:
    """File-backed vector index with embedding-version/retention/deletion lifecycle.

    All public mutations are serialized under a per-index asyncio lock and
    persisted through the async file helpers, matching SemanticMemoryStore.
    """

    _locks: ClassVar[dict[str, asyncio.Lock]] = {}

    def __init__(self, dir_path: str | Path) -> None:
        self._dir = Path(dir_path).resolve() / "l2"
        self._path = self._dir / "vectors.json"
        self._lock = self._locks.setdefault(str(self._path), asyncio.Lock())

    # ── persistence ──────────────────────────────────────────────────────

    async def _read_entries(self) -> list[L2VectorEntry]:
        if not await aexists(self._path):
            return []
        try:
            data = await aread_json(self._path)
        except (OSError, ValueError, TypeError):
            return []
        return [L2VectorEntry(**item) for item in data]

    async def _write_entries(self, entries: list[L2VectorEntry]) -> None:
        await amkdir(self._dir)
        await awrite_json(self._path, [entry.__dict__ for entry in entries])

    # ── lifecycle ────────────────────────────────────────────────────────

    async def upsert(self, entry: L2VectorEntry) -> None:
        """Insert or replace the entry for ``record_id``."""
        async with self._lock:
            entries = await self._read_entries()
            entries = [existing for existing in entries if existing.record_id != entry.record_id]
            entries.append(entry)
            entries.sort(key=lambda item: item.record_id)
            await self._write_entries(entries)

    async def get(self, record_id: str) -> L2VectorEntry | None:
        entries = await self._read_entries()
        return next((entry for entry in entries if entry.record_id == record_id), None)

    async def search(
        self,
        embedding: list[float],
        *,
        scope: str | None = None,
        session_ids: list[str] | None = None,
        limit: int = 6,
    ) -> list[tuple[float, L2VectorEntry]]:
        """Cosine search filtered by scope/session, excluding expired/tombstones."""
        if not embedding or limit <= 0:
            return []
        now = _now_iso()
        session_filter = set(session_ids or [])
        scored: list[tuple[float, L2VectorEntry]] = []
        for entry in await self._read_entries():
            if entry.tombstone or _iso_leq(entry.expires_at, now):
                continue
            if scope is not None and entry.scope != scope:
                continue
            if session_filter and entry.session_id not in session_filter:
                continue
            scored.append((_cosine(embedding, entry.vector), entry))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return scored[:limit]

    async def delete(self, record_ids: list[str]) -> int:
        """Hard-delete entries; returns the number removed."""
        if not record_ids:
            return 0
        target = set(record_ids)
        async with self._lock:
            entries = await self._read_entries()
            kept = [entry for entry in entries if entry.record_id not in target]
            if len(kept) == len(entries):
                return 0
            await self._write_entries(kept)
            return len(entries) - len(kept)

    async def tombstone_by_source(
        self,
        session_ids: list[str],
        record_ids: list[str] | None = None,
    ) -> int:
        """Mark entries of a deleted source as tombstones (deletion propagation).

        Vectors are not physically removed here so history stays replayable;
        call :meth:`prune` to purge them.
        """
        if not session_ids:
            return 0
        session_filter = set(session_ids)
        id_filter = set(record_ids or [])
        async with self._lock:
            entries = await self._read_entries()
            changed = 0
            for entry in entries:
                if entry.tombstone:
                    continue
                if entry.session_id not in session_filter:
                    continue
                if id_filter and entry.record_id not in id_filter:
                    continue
                entry.tombstone = True
                entry.updated_at = _now_iso()
                changed += 1
            if changed:
                await self._write_entries(entries)
            return changed

    async def prune(self, now: str | None = None) -> dict[str, int]:
        """Remove expired and tombstoned entries (retention sweep)."""
        now = now or _now_iso()
        async with self._lock:
            entries = await self._read_entries()
            kept = [
                entry
                for entry in entries
                if not entry.tombstone and not _iso_leq(entry.expires_at, now)
            ]
            purged = len(entries) - len(kept)
            if purged:
                await self._write_entries(kept)
            return {"purged": purged, "remaining": len(kept)}

    async def stale_ids(self, current_version: str) -> list[str]:
        """ids whose embedding version differs from the active one."""
        entries = await self._read_entries()
        return [
            entry.record_id
            for entry in entries
            if not entry.tombstone and entry.embedding_version != current_version
        ]

    async def metrics(self, current_version: str | None = None) -> dict[str, Any]:
        entries = await self._read_entries()
        now = _now_iso()
        version = current_version or EmbeddingVersion.current().tag
        return {
            "total": len(entries),
            "active": sum(1 for entry in entries if not entry.tombstone and not _iso_leq(entry.expires_at, now)),
            "tombstoned": sum(1 for entry in entries if entry.tombstone),
            "expired": sum(1 for entry in entries if not entry.tombstone and _iso_leq(entry.expires_at, now)),
            "stale": sum(1 for entry in entries if not entry.tombstone and entry.embedding_version != version),
        }