"""Small, dependency-free SSE frame parser used by the CLI transport."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Iterator


@dataclass(frozen=True)
class SseFrame:
    """One decoded SSE frame, excluding comment-only heartbeats."""

    data: str
    event: str = "message"
    event_id: str = ""
    retry: int | None = None


def iter_sse_frames(lines: Iterable[str | bytes]) -> Iterator[SseFrame]:
    """Yield frames according to the SSE field and blank-line rules."""
    data: list[str] = []
    event = "message"
    event_id = ""
    retry: int | None = None

    def flush() -> SseFrame | None:
        nonlocal data, event, event_id, retry
        if not data:
            event = "message"
            event_id = ""
            retry = None
            return None
        frame = SseFrame("\n".join(data), event, event_id, retry)
        data = []
        event = "message"
        event_id = ""
        retry = None
        return frame

    for raw_line in lines:
        line = raw_line.decode("utf-8", "replace") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.rstrip("\r\n")
        if not line:
            frame = flush()
            if frame is not None:
                yield frame
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
        elif field == "event":
            event = value
        elif field == "id" and "\x00" not in value:
            event_id = value
        elif field == "retry":
            try:
                parsed = int(value)
            except ValueError:
                continue
            if parsed >= 0:
                retry = parsed
    frame = flush()
    if frame is not None:
        yield frame


__all__ = ["SseFrame", "iter_sse_frames"]
