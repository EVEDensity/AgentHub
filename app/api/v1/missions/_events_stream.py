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
    last_event_id = request.headers.get("last-event-id", "").strip()
    if last_event_id and after_sequence == 0:
        try:
            after_sequence = await repository.event_sequence(last_event_id)
        except Exception:  # noqa: BLE001 - stale/unknown IDs simply catch up from zero
            after_sequence = 0

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
                        f"data: {json.dumps(_sse_public_event(event), ensure_ascii=False)}\n\n"
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


def _sse_public_event(event: dict) -> dict:
    """Convert legacy repository dictionaries to the canonical SSE envelope."""
    aggregate_type = event.get("aggregate_type", event.get("aggregateType", ""))
    aggregate_id = event.get("aggregate_id", event.get("aggregateId", ""))
    canonical = {
        "schemaVersion": int(event.get("schema_version", event.get("schemaVersion", 1))),
        "eventId": event.get("event_id", event.get("eventId", "")),
        "missionId": event.get("correlation_id", event.get("correlationId", event.get("mission_id", ""))),
        "aggregate": {
            "type": getattr(aggregate_type, "value", aggregate_type),
            "id": aggregate_id,
            "sequence": int(event.get("sequence", 0)),
        },
        "type": event.get("event_type", event.get("eventType", event.get("type", ""))),
        "payload": event.get("payload", {}),
    }
    # Keep legacy keys during the migration so older CLI/web consumers remain
    # functional while new clients use the versioned envelope above.
    canonical.update({
        "event_id": canonical["eventId"],
        "aggregate_type": canonical["aggregate"]["type"],
        "aggregate_id": canonical["aggregate"]["id"],
        "sequence": canonical["aggregate"]["sequence"],
        "event_type": canonical["type"],
        "correlation_id": canonical["missionId"],
    })
    return canonical
