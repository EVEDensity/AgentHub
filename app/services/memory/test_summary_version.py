from __future__ import annotations

import asyncio

from app.services.memory.session_memory import SessionMemoryManager
from app.services.memory.storage import MemoryStorage
from app.services.memory.summary_version import SummaryVersion, should_accept_summary


def test_summary_version_rejects_duplicate_and_older_coverage() -> None:
    current = SummaryVersion(1, 40, 200.0, "event-2")
    assert not should_accept_summary(current, SummaryVersion(1, 50, 210.0, "event-2"))
    assert not should_accept_summary(current, SummaryVersion(1, 30, 210.0, "event-3"))
    assert not should_accept_summary(current, SummaryVersion(1, 50, 190.0, "event-4"))


def test_summary_version_accepts_newer_coverage() -> None:
    current = SummaryVersion(1, 40, 200.0, "event-2")
    assert should_accept_summary(current, SummaryVersion(20, 60, 210.0, "event-3"))


def test_session_summary_store_rejects_stale_write(tmp_path) -> None:
    async def run() -> None:
        manager = SessionMemoryManager(MemoryStorage(tmp_path / "memory"))
        assert await manager.write_session_summary(
            "s1", "new summary", covered_sequence_end=40,
            generated_at=200.0, source_event_id="event-2", force=False,
        )
        assert not await manager.write_session_summary(
            "s1", "stale summary", covered_sequence_end=30,
            generated_at=210.0, source_event_id="event-3", force=False,
        )
        assert await manager.get_session_summary("s1") == "new summary"
        assert await manager.write_session_summary(
            "s1", "newest summary", covered_sequence_start=20, covered_sequence_end=60,
            generated_at=220.0, source_event_id="event-4", force=False,
        )
        assert await manager.get_session_summary("s1") == "newest summary"

    asyncio.run(run())
