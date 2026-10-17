"""Versioned native Session history shapes match real HTTP read projections."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from pydantic import TypeAdapter, ValidationError as ProducerValidationError

from app.schemas.session_history import SessionDetail, SessionDirectory, SessionMissionDirectory
from tests.integration.test_native_session_history import admit, directory, missions
from tests.integration.test_mission_chat_http_fixture import (  # noqa: F401 - actual SQLite fixture
    HUMAN, WORKSPACE_ID, chat_http,
)

SCHEMA = json.loads((Path(__file__).parents[2] / "platform/contracts/v1/session-history.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def documents(http, *, message="中文 😀 goal"):
    admitted = admit(http, message)
    detail = http.get(f"/api/v1/sessions/{admitted['sessionId']}", headers=HUMAN,
                      params={"workspaceId": WORKSPACE_ID})
    assert detail.status_code == 200, detail.text
    return [directory(http).json(), detail.json(), missions(http, admitted["sessionId"]).json()]


def test_schema_is_exact_serialized_dto_union_and_requires_all_wire_fields():
    Draft202012Validator.check_schema(SCHEMA)
    generated = TypeAdapter(SessionDirectory | SessionDetail | SessionMissionDirectory).json_schema(
        by_alias=True, mode="serialization")
    for definition in generated["$defs"].values():
        if definition.get("type") == "object":
            definition["required"] = list(definition["properties"])
    assert SCHEMA["$defs"] == generated["$defs"]
    assert SCHEMA["oneOf"] == generated["anyOf"]
    mission = SCHEMA["$defs"]["SessionMissionItem"]
    assert set(mission["properties"]) == {
        "id", "workspaceId", "sessionId", "title", "objectivePreview", "objectiveTruncated", "status", "createdAt",
    }
    assert mission["properties"]["objectivePreview"]["maxLength"] == 1000
    assert "not an original message receipt" in mission["properties"]["objectivePreview"]["description"]
    assert SCHEMA["$defs"]["SessionDirectory"]["properties"]["sessions"]["maxItems"] == 50
    assert SCHEMA["$defs"]["SessionMissionDirectory"]["properties"]["missions"]["maxItems"] == 20


def test_actual_sqlite_http_producer_emits_each_contract_variant_and_unicode_limit(chat_http):
    outputs = documents(chat_http, message="😀" * 1001)
    for document in outputs:
        VALIDATOR.validate(document)
        restored = TypeAdapter(SessionDirectory | SessionDetail | SessionMissionDirectory).validate_python(document)
        assert restored.to_public_dict() == document
    item = outputs[2]["missions"][0]
    assert item["objectivePreview"] == "😀" * 1000
    assert item["objectiveTruncated"] is True
    assert item["status"] == "RUNNING"
    assert outputs[0]["sessions"][0] == outputs[1]["session"]
    assert outputs[0]["nextOffset"] is None and outputs[2]["nextBeforeId"] is None


def test_actual_empty_directory_is_explicit_and_valid(chat_http):
    document = directory(chat_http).json()
    VALIDATOR.validate(document)
    assert document["sessions"] == [] and not document["hasMore"] and document["nextOffset"] is None


def test_missing_and_extra_fields_are_rejected_at_every_projection_level(chat_http):
    for document in documents(chat_http):
        locations = [()]
        if "sessions" in document:
            locations.append(("sessions", 0))
        elif "session" in document:
            locations.append(("session",))
        else:
            locations.append(("missions", 0))
        for location in locations:
            original = document
            for component in location:
                original = original[component]
            for key in (*original, "verdict"):
                changed = copy.deepcopy(document)
                target = changed
                for component in location:
                    target = target[component]
                if key == "verdict":
                    target[key] = "PASS"
                else:
                    del target[key]
                with pytest.raises(ValidationError):
                    VALIDATOR.validate(changed)


def test_invalid_versions_types_limits_and_non_goal_fields_are_rejected(chat_http):
    session_page, detail, mission_page = documents(chat_http)
    for original, mutations in (
        (session_page, ({"schemaVersion": 2}, {"schemaVersion": True}, {"hasMore": "false"},
                        {"nextOffset": -1}, {"nextOffset": True}, {"sessions": [detail["session"]] * 51})),
        (detail, ({"session": None}, {"session": {}}, {"workspaceId": ""})),
        (mission_page, ({"nextBeforeId": "bad/id"}, {"missions": [mission_page["missions"][0]] * 21})),
    ):
        for mutation in mutations:
            with pytest.raises(ValidationError):
                VALIDATOR.validate({**original, **mutation})
    for mutation in (
        {"objectivePreview": "😀" * 1001}, {"objectivePreview": None},
        {"objectiveTruncated": "false"}, {"status": "PASS"},
        {"originalMessage": "invented receipt"}, {"evidence": []}, {"source": {}},
    ):
        changed = copy.deepcopy(mission_page)
        changed["missions"][0].update(mutation)
        with pytest.raises(ValidationError):
            VALIDATOR.validate(changed)
    # Minimal jsonschema installs do not provide the optional RFC3339 checker;
    # the producer must still reject invalid or timezone-free timestamps.
    assert SCHEMA["$defs"]["SessionMissionItem"]["properties"]["createdAt"]["format"] == "date-time"
    for timestamp in ("not-a-date", "2026-09-01T09:00:00"):
        changed = copy.deepcopy(mission_page)
        changed["missions"][0]["createdAt"] = timestamp
        with pytest.raises(ProducerValidationError):
            SessionMissionDirectory.model_validate(changed)
