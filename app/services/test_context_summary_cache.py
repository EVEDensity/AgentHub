from __future__ import annotations

from app.services.context_summary_cache import ContextSummaryCache


def test_version_invalidation_forces_summary_rebuild() -> None:
    cache = ContextSummaryCache(ttl_seconds=60)
    calls = 0

    def build() -> str:
        nonlocal calls
        calls += 1
        return f"summary-{calls}"

    assert cache.get_or_build("agent", "u1", "v1", build) == "summary-1"
    assert cache.get_or_build("agent", "u1", "v1", build) == "summary-1"
    cache.invalidate("agent", "u1")
    assert cache.get_or_build("agent", "u1", "v1", build) == "summary-2"
    assert cache.stats()["hits"] == 1


def test_versioned_slot_is_removed_by_owner_invalidation() -> None:
    cache = ContextSummaryCache(ttl_seconds=60)
    cache.set("route", "u1", "active", "route-index")
    assert cache.get("route", "u1", "active") == "route-index"
    cache.invalidate("route", "u1")
    assert cache.get("route", "u1", "active") is None
