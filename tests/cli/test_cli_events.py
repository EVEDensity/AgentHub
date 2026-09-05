from app.cli.events import EventCursor, normalize_event, reorder_events


def test_normalize_legacy_event_and_delta():
    event = normalize_event({
        "eventId": "e1", "eventType": "assistant.delta", "sequence": 3,
        "aggregateType": "mission", "payload": {"text": "hello"},
    })
    assert event is not None
    assert event.event_type == "assistant.delta"
    assert event.text_delta == "hello"
    assert event.status is None


def test_normalize_event_envelope_snake_case_and_delta():
    """Mission Control's Pydantic public envelope is snake_case."""
    event = normalize_event({
        "event_id": "wu-e1",
        "event_type": "harness.assistant.delta",
        "sequence": 6,
        "aggregate_type": "work_unit",
        "aggregate_id": "wu-1",
        "correlation_id": "mis-1",
        "payload": {"attempt": 1, "text": "你好"},
    })
    assert event is not None
    assert event.event_id == "wu-e1"
    assert event.event_type == "assistant.delta"
    assert event.aggregate_type == "work_unit"
    assert event.mission_id == "mis-1"
    assert event.work_unit_id == "wu-1"
    assert event.text_delta == "你好"


def test_cursor_deduplicates_without_advancing_on_work_unit():
    cursor = EventCursor()
    mission = normalize_event({"eventId": "m", "type": "mission.started", "sequence": 4, "aggregateType": "mission"})
    work = normalize_event({"eventId": "w", "type": "tool.started", "sequence": 99, "aggregateType": "work_unit"})
    assert mission is not None and work is not None
    assert cursor.accept(mission)
    assert cursor.sequence == 4
    assert cursor.accept(work)
    assert cursor.sequence == 4
    assert not cursor.accept(mission)


def test_malformed_event_is_ignored():
    assert normalize_event({"eventId": "bad", "sequence": "x"}) is None


def test_normalize_harness_events_to_cli_types():
    event = normalize_event({
        "eventId": "e-tool",
        "eventType": "harness.tool.output",
        "sequence": 5,
        "aggregateType": "work_unit",
        "payload": {"toolName": "shell", "text": "ok"},
    })
    assert event is not None
    assert event.event_type == "tool.output"
    assert event.payload["toolName"] == "shell"


def test_normalize_decision_request_to_pending():
    event = normalize_event({
        "eventId": "e-decision",
        "eventType": "decision.lifecycle.requested",
        "sequence": 6,
        "aggregateType": "decision",
        "payload": {"id": "dec-1", "version": 2},
    })
    assert event is not None
    assert event.event_type == "decision.pending"


def test_reorder_events_sorts_mission_sequences_only():
    events = [
        normalize_event({"eventId": "m2", "type": "mission.started", "sequence": 2, "aggregateType": "mission"}),
        normalize_event({"eventId": "w1", "type": "tool.output", "sequence": 9, "aggregateType": "work_unit"}),
        normalize_event({"eventId": "m1", "type": "mission.created", "sequence": 1, "aggregateType": "mission"}),
    ]
    ordered = reorder_events(event for event in events if event is not None)
    assert [event.event_id for event in ordered] == ["m1", "w1", "m2"]


def test_normalize_all_mission_control_lifecycle_events():
    pairs = {
        "mission.lifecycle.created": "mission.created",
        "work_unit.lifecycle.leased": "work_unit.claimed",
        "work_unit.lifecycle.started": "work_unit.running",
        "work_unit.checkpoint.recorded": "checkpoint.created",
        "artifact.lifecycle.registered": "artifact.registered",
        "mission.lifecycle.verifying": "verification.started",
        "work_unit.lifecycle.verified": "verification.completed",
        "mission.lifecycle.succeeded": "mission.completed",
    }
    for index, (raw_type, expected) in enumerate(pairs.items(), start=1):
        event = normalize_event({"eventId": f"e{index}", "eventType": raw_type, "sequence": index, "aggregateType": "mission", "payload": {}})
        assert event is not None
        assert event.event_type == expected


def test_empty_delta_is_valid_and_does_not_create_text():
    event = normalize_event({"eventId": "empty", "type": "assistant.delta", "sequence": 1, "aggregateType": "mission", "payload": {"text": ""}})
    assert event is not None
    assert event.text_delta is None


def test_reconnect_cursor_accepts_new_event_after_duplicate_batch():
    cursor = EventCursor()
    first = normalize_event({"eventId": "e1", "type": "assistant.delta", "sequence": 1, "aggregateType": "mission", "payload": {"text": "a"}})
    duplicate = normalize_event({"eventId": "e1", "type": "assistant.delta", "sequence": 1, "aggregateType": "mission", "payload": {"text": "a"}})
    resumed = normalize_event({"eventId": "e2", "type": "assistant.delta", "sequence": 2, "aggregateType": "mission", "payload": {"text": "b"}})
    assert first is not None and duplicate is not None and resumed is not None
    assert cursor.accept(first)
    assert not cursor.accept(duplicate)
    assert cursor.accept(resumed)
    assert cursor.sequence == 2


def test_cursor_seen_id_window_is_bounded():
    cursor = EventCursor(max_seen_ids=2)
    for index in range(3):
        event = normalize_event({
            "eventId": f"e{index}",
            "type": "assistant.delta",
            "sequence": index + 1,
            "aggregateType": "mission",
            "payload": {"text": "x"},
        })
        assert event is not None
        assert cursor.accept(event)
    # The oldest ID is evicted, while the most recent duplicate remains
    # protected during a reconnect window.
    oldest = normalize_event({
        "eventId": "e0", "type": "assistant.delta", "sequence": 1,
        "aggregateType": "mission", "payload": {"text": "x"},
    })
    newest = normalize_event({
        "eventId": "e2", "type": "assistant.delta", "sequence": 3,
        "aggregateType": "mission", "payload": {"text": "x"},
    })
    assert oldest is not None and newest is not None
    assert cursor.accept(oldest)
    assert not cursor.accept(newest)
