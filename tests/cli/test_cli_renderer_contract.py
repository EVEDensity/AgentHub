import json

from app.cli.events import normalize_event
from app.cli.reducer import SessionViewState, reduce_event, state_summary, state_to_dict


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
