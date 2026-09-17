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
    assert events[0]["payload"]["missionId"] == "mis-1"
    assert events[0]["payload"]["afterSequence"] == 42


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


def test_sse_connected_exposes_transport_request_id():
    class Transport:
        last_request_id = "req-test"

        @contextmanager
        def stream(self, *args, **kwargs):
            class Response:
                def raise_for_status(self):
                    return None

                def iter_lines(self):
                    return iter(())

            yield Response()

    events = list(SseClient(Transport()).stream_events("mis-1"))
    assert events[0]["payload"]["requestId"] == "req-test"


def test_sse_recovery_probe_reuses_durable_cursor_and_last_event_id():
    class Transport:
        last_request_id = "req-recovery"

        def __init__(self):
            self.calls = []
            self.attempt = 0

        @contextmanager
        def stream(self, *args, **kwargs):
            self.calls.append(kwargs)
            self.attempt += 1
            if self.attempt == 1:
                raise httpx.ReadError("fault injected after durable event")

            class Response:
                def raise_for_status(self):
                    return None

                def iter_lines(self):
                    return iter(
                        [
                            "id: evt-43",
                            'data: {"type":"mission.completed","sequence":43}',
                            "",
                        ]
                    )

            yield Response()

    transport = Transport()
    client = SseClient(transport)
    first = list(
        client.stream_events(
            "mis-1", after_sequence=42, after_event_id="evt-42"
        )
    )
    assert first[0]["type"] == "sse.reconnecting"
    second = list(
        client.stream_events(
            "mis-1", after_sequence=42, after_event_id="evt-42"
        )
    )
    assert second[-1]["eventId"] == "evt-43"
    assert transport.calls[1]["params"]["afterSequence"] == 42
    assert transport.calls[1]["headers"]["Last-Event-ID"] == "evt-42"
