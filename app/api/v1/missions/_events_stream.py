"""v1 missions/_events_stream.py — Mission event ledger and SSE streaming."""
from __future__ import annotations

from app.api.v1.missions._deps import *

router = APIRouter()


@router.get("/{mission_id}/events")
async def list_mission_events(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    after_sequence: EventAfterSequence = 0,
    limit: EventLimit = 100,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    public, _cursor = await _collect_mission_events(
        repository,
        mission_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return {"events": public}

@router.get("/{mission_id}/events/stream")
async def stream_mission_events(
    mission_id: str,
    request: Request,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    after_sequence: EventAfterSequence = 0,
    limit: EventLimit = 100,
    poll_seconds: EventPollSeconds = 1.0,
    max_seconds: EventMaxSeconds = 0,
) -> StreamingResponse:
    """Low-latency SSE stream over the durable mission event ledger.

    The stream subscribes to a process-local wake-up bus before performing
    the initial catch-up read.  Event writes publish a coalesced notification;
    the stream then rereads the ledger and therefore remains lossless even if
    notifications are dropped.  ``pollSeconds`` is retained as the maximum
    notification wait/heartbeat interval for backwards-compatible clients,
    not as the normal database polling cadence.
    """
    mission = await _authorized_mission(mission_id, user=user, repository=repository)
    del mission

    async def event_stream() -> AsyncIterator[str]:
        from app.services.mission_event_bus import mission_event_bus

        cursor = after_sequence
        seen_event_ids: set[str] = set()
        deadline = time.monotonic() + max_seconds if max_seconds > 0 else None
        async with mission_event_bus.subscribe(mission_id) as wakeups:
            while True:
                try:
                    batch, cursor = await _collect_mission_events(
                        repository,
                        mission_id,
                        after_sequence=cursor,
                        limit=limit,
                        seen_event_ids=seen_event_ids,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - keep stream alive for transient DB errors
                    batch = []
                for event in batch:
                    # Include the SSE id so clients can persist Last-Event-ID
                    # while the JSON event remains backwards compatible.
                    yield (
                        f"id: {event.get('event_id', '')}\n"
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    )
                if deadline is not None and time.monotonic() >= deadline:
                    return
                try:
                    if await request.is_disconnected():
                        return
                except Exception:  # noqa: BLE001 - transport already gone
                    return
                remaining = (
                    max(0.0, deadline - time.monotonic())
                    if deadline is not None
                    else None
                )
                wait_for = poll_seconds if remaining is None else min(poll_seconds, remaining)
                if wait_for <= 0:
                    return
                try:
                    await asyncio.wait_for(wakeups.get(), timeout=wait_for)
                except asyncio.TimeoutError:
                    # Heartbeat keeps proxies aware of a live stream.  The
                    # durable ledger is not queried unless a notification
                    # arrives, eliminating per-connection DB polling.
                    yield ": heartbeat\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
