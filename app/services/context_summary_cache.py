from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class _Entry:
    value: Any
    version: int
    expires_at: float


class ContextSummaryCache:
    """Shared versioned cache for compact route and agent prompt summaries."""

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, _Entry] = {}
        self._versions: dict[str, int] = {}
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    @staticmethod
    def _owner_key(kind: str, owner_id: str) -> str:
        return f"{kind}:{owner_id}"

    def get_or_build(
        self,
        kind: str,
        owner_id: str,
        fingerprint_source: str,
        builder: Callable[[], Any],
    ) -> Any:
        owner_key = self._owner_key(kind, owner_id)
        digest = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:24]
        now = time.monotonic()
        with self._lock:
            version = self._versions.get(owner_key, 0)
            cache_key = f"{owner_key}:{version}:{digest}"
            entry = self._entries.get(cache_key)
            if entry and entry.version == version and entry.expires_at > now:
                self._hits += 1
                return entry.value
            self._misses += 1

        value = builder()
        with self._lock:
            self._entries[cache_key] = _Entry(value, version, now + self._ttl)
        return value

    def get(self, kind: str, owner_id: str, slot: str) -> Any | None:
        owner_key = self._owner_key(kind, owner_id)
        now = time.monotonic()
        with self._lock:
            version = self._versions.get(owner_key, 0)
            entry = self._entries.get(f"{owner_key}:{version}:slot:{slot}")
            if entry and entry.expires_at > now:
                self._hits += 1
                return entry.value
            self._misses += 1
            return None

    def set(self, kind: str, owner_id: str, slot: str, value: Any) -> None:
        owner_key = self._owner_key(kind, owner_id)
        with self._lock:
            version = self._versions.get(owner_key, 0)
            self._entries[f"{owner_key}:{version}:slot:{slot}"] = _Entry(
                value, version, time.monotonic() + self._ttl,
            )

    def invalidate(self, kind: str, owner_id: str) -> int:
        owner_key = self._owner_key(kind, owner_id)
        with self._lock:
            self._versions[owner_key] = self._versions.get(owner_key, 0) + 1
            stale = [key for key in self._entries if key.startswith(owner_key + ":")]
            for key in stale:
                self._entries.pop(key, None)
            return self._versions[owner_key]

    def stats(self) -> dict[str, int | float]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
                "hit_ratio": round(self._hits / total, 4) if total else 0.0,
                "versions": len(self._versions),
            }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._versions.clear()
            self._hits = 0
            self._misses = 0


context_summary_cache = ContextSummaryCache()
