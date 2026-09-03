from app.cli.events import normalize_event
from app.cli.reducer import SessionViewState, reduce_event


def event(payload):
    return normalize_event(payload)


def test_reducer_accumulates_text_and_tools():
    state = SessionViewState()
    for raw in (
        {"type": "assistant.delta", "payload": {"text": "Hi"}},
        {"type": "assistant.delta", "payload": {"text": "!"}},
        {"type": "harness.tool.started", "payload": {"toolName": "shell"}},
        {"type": "harness.tool.output", "payload": {"toolName": "shell", "text": "ok"}},
        {"type": "harness.tool.completed", "payload": {"toolName": "shell"}},
    ):
        normalized = event(raw)
        assert normalized is not None
        state = reduce_event(state, normalized)
    assert state.assistant_text == "Hi!"
    assert state.tools[0].status == "completed"
    assert state.tools[0].output == "ok"


def test_reducer_tracks_and_clears_decision():
    pending = event({"type": "decision.pending", "payload": {"id": "d1", "version": 1}})
    resolved = event({"type": "decision.lifecycle.resolved", "payload": {}})
    assert pending is not None and resolved is not None
    state = reduce_event(SessionViewState(), pending)
    assert state.pending_decision is not None
    assert reduce_event(state, resolved).pending_decision is None


def test_reducer_tracks_verification_status():
    started = event({"type": "verification.started", "payload": {}})
    completed = event({"type": "verification.completed", "payload": {}})
    assert started is not None and completed is not None
    state = reduce_event(SessionViewState(), started)
    assert state.verification_status == "started"
    assert reduce_event(state, completed).verification_status == "completed"
