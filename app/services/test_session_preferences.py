from __future__ import annotations

from app.services.session_preferences import (
    apply_session_pin_state,
    parse_pinned_session_ids,
    serialize_pinned_session_ids,
)


def test_parse_pinned_session_ids_handles_invalid_json() -> None:
    assert parse_pinned_session_ids("not-json") == set()


def test_parse_pinned_session_ids_parses_list() -> None:
    assert parse_pinned_session_ids('["a", "b", ""]') == {"a", "b"}


def test_serialize_pinned_session_ids_is_stable() -> None:
    assert serialize_pinned_session_ids({"b", "a"}) == '["a", "b"]'


def test_apply_session_pin_state_sorts_pinned_first() -> None:
    sessions = [
        {"id": "s2", "createdAt": "2026-07-18T10:00:00"},
        {"id": "s1", "createdAt": "2026-07-18T09:00:00"},
    ]
    ordered = apply_session_pin_state(sessions, {"s1"})
    assert ordered[0]["id"] == "s1"
    assert ordered[0]["isPinned"] == 1
