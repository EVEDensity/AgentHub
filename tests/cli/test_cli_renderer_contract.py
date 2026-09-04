import json
from unittest import mock

from app.cli.events import normalize_event
from app.cli.reducer import SessionViewState, reduce_event, state_summary, state_to_dict
from app.cli.runtime import MissionControlClient
from rich.console import Console
from app.cli.ui import render_state_panel


def test_all_renderers_share_canonical_reducer_snapshot():
    state = SessionViewState()
    for raw in (
        {"type": "mission.created", "status": "RUNNING", "payload": {}},
        {"type": "assistant.delta", "payload": {"text": "Inspecting"}},
        {"type": "harness.tool.started", "payload": {"toolName": "file_read"}},
        {"type": "harness.tool.output", "payload": {"toolName": "file_read", "text": "README"}},
        {"type": "decision.pending", "payload": {"decision": {"id": "d1"}}},
        {"type": "verification.started", "payload": {}},
    ):
        event = normalize_event(raw)
        assert event is not None
        state = reduce_event(state, event)
    snapshot = state_to_dict(state)
    assert json.loads(json.dumps(snapshot, ensure_ascii=False)) == snapshot
    assert state_summary(state) == "CREATED · tool:file_read output · decision pending · verification:started"
    assert snapshot["assistantText"] == "Inspecting"
    assert snapshot["tools"][0]["output"] == "README"


def test_unknown_event_is_diagnostic_only():
    event = normalize_event({"type": "future.event", "eventId": "x", "sequence": 1, "aggregateType": "mission", "payload": {}})
    assert event is not None
    state = reduce_event(SessionViewState(), event)
    assert state.diagnostics == ("unknown event: future.event",)
    assert state.assistant_text == ""


def test_connection_and_terminal_error_states_are_shared():
    state = SessionViewState()
    for raw in (
        {"type": "sse.reconnecting", "payload": {}},
        {"type": "sse.polling", "payload": {}},
        {"type": "sse.connected", "payload": {}},
        {"type": "mission.failed", "payload": {}},
    ):
        event = normalize_event(raw)
        assert event is not None
        state = reduce_event(state, event)
    snapshot = state_to_dict(state)
    assert snapshot["connectionStatus"] == "connected"
    assert snapshot["status"] == "FAILED"
    assert state_summary(state).startswith("FAILED")


def test_renderers_project_same_snapshot_after_reconnect_and_decision():
    state = SessionViewState()
    for raw in (
        {"type": "sse.reconnecting", "payload": {}},
        {"type": "decision.pending", "payload": {"decision": {"id": "d-1", "version": 2}}},
        {"type": "sse.connected", "payload": {}},
    ):
        event = normalize_event(raw)
        assert event is not None
        state = reduce_event(state, event)

    snapshot = state_to_dict(state)
    assert snapshot["connectionStatus"] == "connected"
    assert snapshot["pendingDecision"]["id"] == "d-1"
    console = Console(width=80, force_terminal=False)
    with console.capture() as capture:
        console.print(render_state_panel(state))
    rendered = capture.get()
    assert "decision pending" in rendered
    # JSONL and Rich both consume the exact same reducer snapshot.
    assert state_summary(state) == "CREATED · decision pending · stream:connected"


def test_sse_client_surfaces_connected_and_reconnecting_states():
    class _Response:
        def raise_for_status(self):
            raise RuntimeError("connection lost")

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *args):
            return False

    client = MissionControlClient("http://test")
    client._client = mock.Mock()
    client._token = "test-token"
    client._client.stream.return_value = _Stream()
    events = list(client.stream_events("m-1"))
    assert len(events) == 1
    assert events[0]["type"] == "sse.reconnecting"


def test_sse_client_emits_connected_before_frames():
    class _Response:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"type":"mission.created","payload":{}}'
            yield ""

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *args):
            return False

    client = MissionControlClient("http://test")
    client._client = mock.Mock()
    client._token = "test-token"
    client._client.stream.return_value = _Stream()
    events = list(client.stream_events("m-1"))
    assert [event["type"] for event in events] == ["sse.connected", "mission.created"]
