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
    """Server-Sent Events stream over the mission event ledger (P3-4a).

    Reuses the ``GET /events`` query logic and polls it every
    ``pollSeconds`` (default 1 s), pushing each new event as one
    ``data: <json>`` frame. The mission-aggregate sequence acts as the
    cursor; a client disconnect ends the stream. ``maxSeconds`` bounds the
    stream server-side (0 = unlimited).
    """
    mission = await _authorized_mission(mission_id, user=user, repository=repository)
    del mission

    async def event_stream() -> AsyncIterator[str]:
        cursor = after_sequence
        seen_event_ids: set[str] = set()
        deadline = time.monotonic() + max_seconds if max_seconds > 0 else None
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
            except Exception:  # noqa: BLE001 - a failed poll must not kill the stream
                batch = []
            for event in batch:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if deadline is not None and time.monotonic() >= deadline:
                return
            try:
                if await request.is_disconnected():
                    return
            except Exception:  # noqa: BLE001 - transport already gone
                return
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
