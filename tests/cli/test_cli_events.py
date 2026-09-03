from app.cli.events import EventCursor, normalize_event


def test_normalize_legacy_event_and_delta():
    event = normalize_event({
        "eventId": "e1", "eventType": "assistant.delta", "sequence": 3,
        "aggregateType": "mission", "payload": {"text": "hello"},
    })
    assert event is not None
    assert event.event_type == "assistant.delta"
    assert event.text_delta == "hello"
    assert event.status is None


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
