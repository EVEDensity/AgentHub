"""Read durable Mission cursors without treating aggregate sequences as global."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

FetchOne = Callable[..., Awaitable[dict[str, Any] | None]]


async def read_event_sequence(
    fetch_one: FetchOne, event_id: str, *, mission_id: str | None = None,
) -> int:
    if mission_id is None:
        # Preserve the internal legacy lookup. HTTP resume always supplies the
        # authorized Mission and may only advance its Mission aggregate cursor.
        row = await fetch_one("SELECT sequence FROM mission_events WHERE event_id=$1", event_id)
    else:
        row = await fetch_one(
            """SELECT sequence FROM mission_events
               WHERE event_id=$1 AND aggregate_type='mission' AND aggregate_id=$2""",
            event_id, mission_id,
        )
    return int(row["sequence"]) if row is not None else 0
