from app.cli.sse import SseFrame, iter_sse_frames
from app.cli.sse_client import SseClient
from contextlib import contextmanager
import httpx


def test_sse_parser_handles_metadata_multiline_data_and_heartbeat():
    frames = list(iter_sse_frames([
        ": keep-alive",
        "id: evt-1",
        "event: assistant.delta",
        "data: {\"text\":\"hello",
        "data: world\"}",
        "retry: 1500",
        "",
    ]))
    assert frames == [SseFrame('{"text":"hello\nworld"}', "assistant.delta", "evt-1", 1500)]


def test_sse_parser_ignores_invalid_retry_and_nul_ids():
    frames = list(iter_sse_frames([
        "id: bad\x00id",
        "retry: nope",
        "data: ok",
        "",
    ]))
    assert frames == [SseFrame("ok")]


def test_sse_parser_flushes_final_frame_without_blank_line():
    assert list(iter_sse_frames([b"data: final"])) == [SseFrame("final")]


class _BrokenTransport:
    @contextmanager
    def stream(self, *args, **kwargs):
        raise httpx.ReadError("connection dropped")
        yield  # pragma: no cover


def test_sse_client_emits_reconnecting_with_resume_cursor():
    events = list(SseClient(_BrokenTransport()).stream_events("mis-1", after_sequence=42))
    assert len(events) == 1
    assert events[0]["type"] == "sse.reconnecting"
    assert events[0]["payload"]["errorKind"] == "transport"


class _OneBatchTransport:
    @contextmanager
    def stream(self, *args, **kwargs):
        class Response:
            def raise_for_status(self):
                return None

            def iter_lines(self):
                return ["id: evt-43", 'data: {"type":"mission.completed","sequence":43}', ""]

        yield Response()


def test_sse_client_preserves_server_event_id_and_sequence():
    events = list(SseClient(_OneBatchTransport()).stream_events("mis-1", after_sequence=42))
    assert events[0]["type"] == "sse.connected"
    assert events[1]["eventId"] == "evt-43"
    assert events[1]["sequence"] == 43
