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


def iter_sse_frames(
    lines: Iterable[str | bytes],
    *,
    max_line_chars: int = 64 * 1024,
    max_frame_chars: int = 1024 * 1024,
) -> Iterator[SseFrame]:
    """Yield bounded SSE frames according to the field and blank-line rules.

    A malicious or faulty provider must not be able to grow an unbounded
    ``data:`` list.  Oversized frames are discarded at the blank-line
    boundary and parsing then resumes with the next frame.
    """
    if max_line_chars < 1 or max_frame_chars < 1:
        raise ValueError("SSE size limits must be positive")
    data: list[str] = []
    event = "message"
    event_id = ""
    retry: int | None = None
    frame_chars = 0
    oversized = False

    def flush() -> SseFrame | None:
        nonlocal data, event, event_id, retry, frame_chars, oversized
        if not data or oversized:
            event = "message"
            event_id = ""
            retry = None
            data = []
            frame_chars = 0
            oversized = False
            return None
        frame = SseFrame("\n".join(data), event, event_id, retry)
        data = []
        event = "message"
        event_id = ""
        retry = None
        frame_chars = 0
        return frame

    for raw_line in lines:
        line = raw_line.decode("utf-8", "replace") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.rstrip("\r\n")
        if len(line) > max_line_chars:
            oversized = True
            continue
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
            frame_chars += len(value) + (1 if data else 0)
            if frame_chars > max_frame_chars:
                oversized = True
                continue
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
