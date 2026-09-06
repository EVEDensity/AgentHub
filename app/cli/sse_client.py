"""Mission event SSE client built on the shared HTTP transport."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx

from app.cli.errors import classify_error
from app.cli.sse import iter_sse_frames
from app.cli.transport import HttpTransport


class SseClient:
    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    def stream_events(
        self,
        mission_id: str,
        *,
        after_sequence: int = 0,
        after_event_id: str | None = None,
        poll_seconds: float = 0.5,
        max_seconds: float = 2.0,
    ) -> Iterator[dict[str, Any]]:
        timeout = httpx.Timeout(
            connect=5.0,
            read=max(1.0, max_seconds + 1),
            write=10.0,
            pool=10.0,
        )
        try:
            headers = {"Last-Event-ID": after_event_id} if after_event_id else None
            with self._transport.stream(
                "GET",
                f"/api/v1/missions/{mission_id}/events/stream",
                params={
                    "afterSequence": after_sequence,
                    "pollSeconds": poll_seconds,
                    "maxSeconds": max_seconds,
                    **({"afterEventId": after_event_id} if after_event_id else {}),
                },
                headers=headers,
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                yield {
                    "type": "sse.connected",
                    "eventId": f"sse-connected-{uuid.uuid4().hex}",
                    "payload": {"afterSequence": after_sequence},
                }
                for frame in iter_sse_frames(response.iter_lines()):
                    try:
                        event = json.loads(frame.data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if frame.event_id and not event.get("eventId"):
                        event["eventId"] = frame.event_id
                    if frame.event != "message" and not event.get("type"):
                        event["type"] = frame.event
                    yield event
        except (httpx.HTTPError, RuntimeError) as exc:
            yield {
                "type": "sse.reconnecting",
                "eventId": f"sse-reconnecting-{uuid.uuid4().hex}",
                "payload": {
                    "errorType": type(exc).__name__,
                    "errorKind": str(classify_error(exc)),
                },
            }


__all__ = ["SseClient"]
