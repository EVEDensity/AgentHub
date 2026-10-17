"""Real HTTP serialization, private SQLite, Harness streaming and governance."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.integration.mission_chat_driver import AGENT_ID, FIXTURE_TEXT, WORKSPACE_ID
from tests.integration.mission_chat_http_fixture import build_fixture_app

HUMAN = {"Authorization": "Bearer chat-browser-human"}
FOREIGN = {"Authorization": "Bearer chat-browser-foreign"}


@pytest.fixture
def chat_http():
    with TestClient(build_fixture_app()) as http:
        yield http


def admit(http, message="Inspect the browser test"):
    response = http.post("/api/v1/chat/mission", headers=HUMAN,
                         json={"message": message, "workspaceId": WORKSPACE_ID})
    assert response.status_code == 202, response.text
    return response.json()


def advance(http, mission_id, action, **parameters):
    response = http.post(f"/fixture/missions/{mission_id}/advance", headers=HUMAN,
                         json={"action": action, **parameters})
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(http, mission_id):
    response = http.get(f"/fixture/missions/{mission_id}", headers=HUMAN)
    assert response.status_code == 200, response.text
    return response.json()


def status(http, mission_id):
    response = http.get(f"/api/v1/missions/{mission_id}/execution-status", headers=HUMAN)
    assert response.status_code == 200, response.text
    return response.json()


def frames(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def event_ids(events, key="eventId"):
    return {event[key] for event in events}


def assert_harness_observations(persisted):
    events = [event for event in persisted["events"] if event["event_type"].startswith("harness.assistant.")]
    assert len(events) == 3
    assert "".join(event["payload"]["text"] for event in events
                   if event["event_type"] == "harness.assistant.delta") == FIXTURE_TEXT
    complete = next(event for event in events if event["event_type"] == "harness.assistant.completed")
    assert complete["payload"]["text"] == FIXTURE_TEXT
    return events


def assert_resumed_mission_cursor(http, mission_id, persisted, text_events):
    events = [event for event in persisted["events"] if event["aggregate_type"] == "mission"]
    last = max(events, key=lambda event: event["sequence"])
    resumed = frames(http.get(f"/api/v1/missions/{mission_id}/events/stream", headers={
        **HUMAN, "Last-Event-ID": last["event_id"],
    }, params={"maxSeconds": 0.01}))
    # Mission cursors do not consume WorkUnit aggregate sequences. Replayed
    # WorkUnit events keep their durable IDs so the observer can deduplicate.
    assert event_ids(text_events, "event_id") <= event_ids(resumed)
    assert last["event_id"] not in event_ids(resumed)


def assert_pending_governance(persisted):
    assert persisted["mission"]["status"] == "WAITING_DECISION"
    assert persisted["workUnits"][0]["status"] == "VERIFYING"
    assert persisted["evidence"] == []
    decision = persisted["decisions"][0]
    assert decision["status"] == "PENDING"
    assert decision["reasonCode"] == "artifact_requirements_not_met"
    assert decision["criterionIds"] == ["desktop-artifacts"]
    return decision


def test_boot_has_real_catalog_but_admission_creates_the_first_mission(chat_http):
    response = chat_http.get("/api/v1/missions", headers=HUMAN, params={"workspaceId": WORKSPACE_ID})
    assert response.json()["missions"] == []
    members = chat_http.get(f"/api/v1/workspaces/{WORKSPACE_ID}/members", headers=HUMAN).json()
    assert any(item["memberId"] == AGENT_ID and item["adapterType"] == "function-calling"
               for item in members["members"])
    body = admit(chat_http)
    persisted = snapshot(chat_http, body["missionId"])
    assert persisted["mission"]["source"]["sessionId"] == body["sessionId"]
    assert persisted["mission"]["status"] == "RUNNING"
    assert body["dispatch"]["workUnitId"] == persisted["workUnits"][0]["id"]
    assert body["dispatch"]["status"] == persisted["workUnits"][0]["status"] == "PENDING"
    assert persisted["workUnits"][0]["attempt"] == 0
    assert persisted["evidence"] == persisted["artifacts"] == []
    projected = status(chat_http, body["missionId"])
    assert projected["workUnits"][0]["reason"] == "waiting_runner"
    assert projected["workUnits"][0]["matchingRunnerAvailable"] is False
    events = chat_http.get(f"/api/v1/sessions/{body['sessionId']}/events", headers=HUMAN,
                           params={"workspaceId": WORKSPACE_ID}).json()["events"]
    assert {item["eventType"] for item in events} >= {"message.created", "mission.created"}


def test_real_scope_and_request_validation_refuse_without_creating_missions(chat_http):
    for headers, body, expected in (
        (FOREIGN, {"message": "forbidden", "workspaceId": WORKSPACE_ID}, 403),
        ({}, {"message": "unauthenticated", "workspaceId": WORKSPACE_ID}, 401),
        (HUMAN, {"message": "unsupported", "workspaceId": WORKSPACE_ID, "attachments": [{}]}, 422),
    ):
        response = chat_http.post("/api/v1/chat/mission", headers=headers, json=body)
        assert response.status_code == expected, response.text
    assert chat_http.get("/api/v1/missions", headers=HUMAN,
                         params={"workspaceId": WORKSPACE_ID}).json()["missions"] == []
    body = admit(chat_http)
    mission_id = body["missionId"]
    for suffix in ("", "/execution-status", "/events", "/events/stream?maxSeconds=0.01"):
        assert chat_http.get(f"/api/v1/missions/{mission_id}{suffix}", headers=FOREIGN).status_code == 403
    assert chat_http.get(f"/fixture/missions/{mission_id}", headers=FOREIGN).status_code == 403
    assert chat_http.post(f"/api/v1/missions/{mission_id}/cancel", headers=FOREIGN).status_code == 403
    assert snapshot(chat_http, mission_id)["mission"]["status"] == "RUNNING"


def test_cancel_is_a_real_transaction_and_closes_work_without_pass(chat_http):
    mission_id = admit(chat_http)["missionId"]
    response = chat_http.post(f"/api/v1/missions/{mission_id}/cancel", headers=HUMAN, json={})
    assert response.status_code == 200
    persisted = snapshot(chat_http, mission_id)
    assert persisted["mission"]["status"] == persisted["workUnits"][0]["status"] == "CANCELLED"
    assert persisted["evidence"] == persisted["artifacts"] == []
    assert status(chat_http, mission_id)["workUnits"][0]["reason"] == "cancelled"
    assert chat_http.post(f"/fixture/missions/{mission_id}/advance", headers=HUMAN,
                          json={"action": "start"}).status_code == 409
    counters = chat_http.get("/fixture/health").json()["commandCounters"]
    assert counters[f"POST /api/v1/missions/{mission_id}/cancel"] == 1


def test_real_harness_text_replays_stable_ledger_ids_without_state_mutation(chat_http):
    body = admit(chat_http)
    mission_id = body["missionId"]
    advance(chat_http, mission_id, "start")
    before_text = status(chat_http, mission_id)
    assert before_text["workUnits"][0]["reason"] == "executing"
    assert before_text["workUnits"][0]["availabilitySource"] == "lease"
    persisted = advance(chat_http, mission_id, "text")
    text_events = assert_harness_observations(persisted)
    stream_path = f"/api/v1/missions/{mission_id}/events/stream"
    first = frames(chat_http.get(stream_path, headers=HUMAN, params={"maxSeconds": 0.01}))
    second = frames(chat_http.get(stream_path, headers=HUMAN, params={"maxSeconds": 0.01}))
    assert event_ids(first) == event_ids(second)
    assert event_ids(text_events, "event_id") <= event_ids(first)
    assert_resumed_mission_cursor(chat_http, mission_id, persisted, text_events)
    assert snapshot(chat_http, mission_id) == persisted
    assert persisted["evidence"] == []
    assert persisted["mission"]["status"] == "RUNNING"
    # Calling the fixture's text observation twice does not invent new output.
    assert advance(chat_http, mission_id, "text") == persisted


def test_real_session_keyset_cursor_replays_only_the_remaining_persisted_events(chat_http):
    body = admit(chat_http)
    path = f"/api/v1/sessions/{body['sessionId']}/events"
    parameters = {"workspaceId": WORKSPACE_ID, "limit": 1}
    first = chat_http.get(path, headers=HUMAN, params=parameters).json()
    assert len(first["events"]) == 1 and first["hasMore"]
    assert first["nextAfterId"] == first["events"][0]["id"]
    remaining = chat_http.get(path, headers=HUMAN, params={
        "workspaceId": WORKSPACE_ID, "afterId": first["nextAfterId"],
    }).json()
    streamed = frames(chat_http.get(path + "/stream", headers=HUMAN, params={
        "workspaceId": WORKSPACE_ID, "afterId": first["nextAfterId"],
        "maxSeconds": 0.01, "pollSeconds": 0.11,
    }))
    assert streamed == remaining["events"]
    assert {event["id"] for event in first["events"]}.isdisjoint(event["id"] for event in streamed)


def test_real_one_second_lease_expires_without_fabricated_status_or_text(chat_http):
    mission_id = admit(chat_http)["missionId"]
    advance(chat_http, mission_id, "start", leaseSeconds=1)
    assert status(chat_http, mission_id)["workUnits"][0]["reason"] == "executing"
    persisted = advance(chat_http, mission_id, "expire")
    projected = status(chat_http, mission_id)
    assert persisted["workUnits"][0]["status"] == "RUNNING"
    assert projected["workUnits"][0]["reason"] == "lease_expired"
    assert projected["workUnits"][0]["matchingRunnerAvailable"] is False
    assert chat_http.post(f"/fixture/missions/{mission_id}/advance", headers=HUMAN,
                          json={"action": "text"}).status_code == 409
    assert snapshot(chat_http, mission_id) == persisted


def test_targeted_fixture_lease_changes_only_the_selected_pending_session(chat_http):
    other = admit(chat_http)
    selected = admit(chat_http)
    assert other["sessionId"] != selected["sessionId"]
    untouched = snapshot(chat_http, other["missionId"])
    assert untouched["workUnits"][0]["status"] == "PENDING"
    assert untouched["workUnits"][0]["attempt"] == 0
    before = snapshot(chat_http, selected["missionId"])
    assert before["workUnits"][0]["status"] == "PENDING"
    changed = advance(chat_http, selected["missionId"], "start", leaseSeconds=1)
    unit = changed["workUnits"][0]
    assert unit["status"] == "RUNNING" and unit["attempt"] == 1
    assert unit["assignedAgentId"] == AGENT_ID
    assert unit["lease"]["runnerId"] == "chat-browser-runner"
    assert unit["lease"]["id"].startswith("lease-")
    assert status(chat_http, selected["missionId"])["workUnits"][0]["availabilitySource"] == "lease"
    assert snapshot(chat_http, other["missionId"]) == untouched
    # The selected live lease supplies real workspace runner availability;
    # the untouched WorkUnit is still merely waiting for its own claim.
    assert status(chat_http, other["missionId"])["workUnits"][0]["reason"] == "waiting_claim"


def test_real_output_registration_and_completion_stop_at_verifying(chat_http):
    mission_id = admit(chat_http)["missionId"]
    persisted = advance(chat_http, mission_id, "complete")
    assert persisted["mission"]["status"] == "RUNNING"
    assert persisted["workUnits"][0]["status"] == "VERIFYING"
    assert "lease" not in persisted["workUnits"][0]
    assert persisted["evidence"] == []
    assert status(chat_http, mission_id)["workUnits"][0]["reason"] == "verifying"
    artifact = persisted["artifacts"][0]
    assert artifact["kind"] == "test-result"
    response = chat_http.get(f"/api/v1/missions/{mission_id}/artifacts/{artifact['id']}/preview", headers=HUMAN)
    assert response.status_code == 200, response.text
    assert response.json()["text"] == FIXTURE_TEXT
    assert snapshot(chat_http, mission_id) == persisted


def test_real_policy_requests_decision_and_human_resolution_cannot_create_pass(chat_http):
    mission_id = admit(chat_http)["missionId"]
    persisted = advance(chat_http, mission_id, "decision")
    decision = assert_pending_governance(persisted)
    inbox = chat_http.get("/api/v1/missions/decisions", headers=HUMAN,
                          params={"workspaceId": WORKSPACE_ID}).json()["decisions"]
    assert inbox == [decision]
    path = f"/api/v1/missions/{mission_id}/decisions/{decision['id']}/resolve"
    command = {"expectedVersion": decision["version"], "resolution": "FAIL_MISSION",
               "rationale": "Browser fixture deliberately cannot prove the required Artifact set"}
    assert chat_http.post(path, headers=FOREIGN, json=command).status_code == 403
    assert chat_http.post(path, headers=HUMAN,
                          json={**command, "expectedVersion": 2}).status_code == 409
    assert snapshot(chat_http, mission_id) == persisted
    response = chat_http.post(path, headers=HUMAN, json=command)
    assert response.status_code == 200, response.text
    terminal = snapshot(chat_http, mission_id)
    assert terminal["mission"]["status"] == terminal["workUnits"][0]["status"] == "FAILED"
    assert terminal["decisions"][0]["status"] == "RESOLVED"
    assert terminal["evidence"] == []
    assert chat_http.post(path, headers=HUMAN, json=command).status_code == 409


def test_private_project_rule_uses_real_pending_confirmation_and_cancellation(chat_http):
    pending = admit(chat_http, "fixture-confirm inspect")
    assert pending["status"] == "pending"
    assert "missionId" not in pending
    assert chat_http.get("/api/v1/missions", headers=HUMAN,
                         params={"workspaceId": WORKSPACE_ID}).json()["missions"] == []
    command = {"pendingId": pending["pendingId"]}
    confirmed = chat_http.post("/api/v1/chat/confirm", headers=HUMAN, json=command)
    assert confirmed.status_code == 202, confirmed.text
    assert snapshot(chat_http, confirmed.json()["missionId"])["workUnits"][0]["status"] == "PENDING"
    assert chat_http.post("/api/v1/chat/confirm", headers=HUMAN, json=command).status_code == 409
    pending = admit(chat_http, "fixture-confirm cancel")
    response = chat_http.post("/api/v1/chat/cancel", headers=HUMAN,
                              json={"pendingId": pending["pendingId"]})
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert len(chat_http.get("/api/v1/missions", headers=HUMAN,
                             params={"workspaceId": WORKSPACE_ID}).json()["missions"]) == 1


def test_fixture_shutdown_removes_owned_database_and_cas():
    app = build_fixture_app()
    with TestClient(app) as http:
        root = app.state.root
        mission_id = admit(http)["missionId"]
        advance(http, mission_id, "complete")
        assert (root / "missions.sqlite3").is_file()
        assert (root / "cas" / "sha256").is_dir()
    assert not root.exists()
