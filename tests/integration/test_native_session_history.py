"""Native history projections over actual admission, SQLite and workspace ACL."""
from datetime import UTC, datetime

from app.api.v1.sessions import get_session_mission_history_repository, get_session_repository
from app.domain import ActorRef, MissionSource, Session
from app.repositories import MissionRepository, SessionEventRepository, SessionRepository
from app.services.auth_service import get_current_user
from tests.integration.mission_chat_driver import WORKSPACE_ID
from tests.integration.test_mission_chat_http_fixture import (  # noqa: F401 - shared real fixture
    FOREIGN, HUMAN, chat_http,
)


def admit(http, message="Native history request", session_id=None):
    response = http.post("/api/v1/chat/mission", headers=HUMAN, json={
        "message": message, "workspaceId": WORKSPACE_ID, "sessionId": session_id,
    })
    assert response.status_code == 202, response.text
    return response.json()


def directory(http, **parameters):
    return http.get("/api/v1/sessions", headers=HUMAN,
                    params={"workspaceId": WORKSPACE_ID, **parameters})


def missions(http, session_id, **parameters):
    return http.get(f"/api/v1/sessions/{session_id}/missions", headers=HUMAN,
                    params={"workspaceId": WORKSPACE_ID, **parameters})


def seed_session(http, identifier, workspace):
    timestamp = datetime.now(UTC)
    value = Session(id=identifier, workspace_id=workspace, title="Private scoped session",
                    created_by=ActorRef(type="human", id=workspace),
                    created_at=timestamp, updated_at=timestamp)
    http.portal.call(SessionRepository().add_session, value)


def test_empty_directory_has_explicit_end_and_no_unscoped_seed_rows(chat_http):
    response = directory(chat_http)
    assert response.status_code == 200
    assert response.json() == {"schemaVersion": 1, "workspaceId": WORKSPACE_ID,
                               "sessions": [], "hasMore": False, "nextOffset": None}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_directory_and_detail_read_actual_native_ownership_and_narrow_metadata(chat_http):
    older = admit(chat_http)
    newer = admit(chat_http)
    seed_session(chat_http, "foreign-native-session", "workspace-chat-foreign")
    page = directory(chat_http, limit=1).json()
    assert [value["id"] for value in page["sessions"]] == [newer["sessionId"]]
    assert page["hasMore"] and page["nextOffset"] == 1
    last = directory(chat_http, limit=1, offset=page["nextOffset"]).json()
    assert [value["id"] for value in last["sessions"]] == [older["sessionId"]]
    assert not last["hasMore"] and last["nextOffset"] is None
    detail = chat_http.get(f"/api/v1/sessions/{older['sessionId']}", headers=HUMAN,
                           params={"workspaceId": WORKSPACE_ID})
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "no-store"
    assert detail.headers["x-content-type-options"] == "nosniff"
    assert set(detail.json()["session"]) == {"id", "workspaceId", "title", "status", "createdAt", "updatedAt"}
    assert detail.json()["session"] == last["sessions"][0]


def test_foreign_unknown_and_legacy_sessions_are_not_relabelled(chat_http):
    from app.db.session import aexecute

    body = admit(chat_http)
    seed_session(chat_http, "foreign-native-session", "workspace-chat-foreign")
    chat_http.portal.call(aexecute,
        "INSERT INTO sessions(id,name,created_at,owner_id) VALUES($1,$2,$3,$4)",
        "legacy-history", "Legacy owner is not workspace authority", datetime.now(UTC).isoformat(), WORKSPACE_ID)
    for identifier in ("missing-session", "legacy-history", "foreign-native-session"):
        for suffix in ("", "/missions"):
            response = chat_http.get(f"/api/v1/sessions/{identifier}{suffix}", headers=HUMAN,
                                     params={"workspaceId": WORKSPACE_ID})
            assert response.status_code == 404
            assert response.json() == {"detail": "Session not found"}
            assert response.headers["cache-control"] == "no-store"
    foreign = chat_http.get(f"/api/v1/sessions/{body['sessionId']}/missions", headers=FOREIGN,
                            params={"workspaceId": WORKSPACE_ID})
    assert foreign.status_code == 403
    assert foreign.headers["cache-control"] == "no-store"
    forbidden_directory = chat_http.get("/api/v1/sessions", headers=FOREIGN,
                                       params={"workspaceId": WORKSPACE_ID})
    assert forbidden_directory.status_code == 403
    assert forbidden_directory.headers["cache-control"] == "no-store"
    assert chat_http.get("/api/v1/sessions", params={"workspaceId": WORKSPACE_ID}).status_code == 401


def test_access_checks_precede_history_query_and_failures_do_not_become_empty_history(chat_http):
    body = admit(chat_http)

    class UnavailableHistory:
        calls = 0

        async def list_for_session(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("private SQL, credentials and storage addresses")

    unavailable = UnavailableHistory()
    chat_http.app.dependency_overrides[get_session_mission_history_repository] = lambda: unavailable
    path = f"/api/v1/sessions/{body['sessionId']}/missions"
    response = chat_http.get(path, headers=FOREIGN, params={"workspaceId": WORKSPACE_ID})
    assert response.status_code == 403 and unavailable.calls == 0
    response = missions(chat_http, "missing-session")
    assert response.status_code == 404 and unavailable.calls == 0
    response = missions(chat_http, body["sessionId"])
    assert response.status_code == 503 and unavailable.calls == 1
    assert response.json() == {"detail": "Session history is unavailable"}
    assert response.headers["cache-control"] == "no-store"


def test_machine_principals_cannot_read_personal_history_even_with_workspace_id(chat_http):
    body = admit(chat_http)

    class NoSessionReads:
        async def get_session(self, *args):
            raise AssertionError("Unauthorized Session lookup")

        async def list_sessions(self, *args, **kwargs):
            raise AssertionError("Unauthorized Session directory query")

    chat_http.app.dependency_overrides[get_session_repository] = NoSessionReads
    for role in ("runner", "verifier", "agent", "service"):
        chat_http.app.dependency_overrides[get_current_user] = lambda role=role: {"id": WORKSPACE_ID, "role": role}
        for path in ("/api/v1/sessions", f"/api/v1/sessions/{body['sessionId']}",
                     f"/api/v1/sessions/{body['sessionId']}/missions"):
            response = chat_http.get(path, headers=HUMAN, params={"workspaceId": WORKSPACE_ID})
            assert response.status_code == 403
            assert response.json() == {"detail": "Human Session history access required"}
            assert response.headers["cache-control"] == "no-store"


def test_mission_directory_is_narrow_objective_excerpt_and_keeps_real_status(chat_http):
    text = "😀" * 1001
    body = admit(chat_http, text)
    page = missions(chat_http, body["sessionId"])
    assert page.status_code == 200, page.text
    document = page.json()
    assert document["schemaVersion"] == 1 and document["sessionId"] == body["sessionId"]
    item = document["missions"][0]
    assert set(item) == {"id", "workspaceId", "sessionId", "title", "objectivePreview",
                         "objectiveTruncated", "status", "createdAt"}
    assert item["objectivePreview"] == "😀" * 1000 and item["objectiveTruncated"] is True
    assert item["status"] == "RUNNING" and item["id"] == body["missionId"]
    assert document["nextBeforeId"] is None and not document["hasMore"]
    assert page.headers["cache-control"] == "no-store"


def test_session_events_cannot_reassign_actual_mission_source_binding(chat_http):
    from app.domain import SessionEvent, SessionEventType

    first, other = admit(chat_http), admit(chat_http)
    event = SessionEvent(id="evt-misleading-session-link", session_id=first["sessionId"],
                         event_type=SessionEventType.MISSION_CREATED,
                         actor=ActorRef(type="human", id=WORKSPACE_ID),
                         payload={"mission_id": other["missionId"]}, created_at=datetime.now(UTC))
    chat_http.portal.call(SessionEventRepository().add_session_event, event)
    page = missions(chat_http, first["sessionId"]).json()
    assert [value["id"] for value in page["missions"]] == [first["missionId"]]


def test_mission_keyset_pages_do_not_duplicate_when_a_newer_mission_is_inserted(chat_http):
    first = admit(chat_http)
    values = [first]
    for index in range(22):
        values.append(admit(chat_http, f"Native turn {index}", first["sessionId"]))
    initial = missions(chat_http, first["sessionId"], limit=20).json()
    assert initial["hasMore"] and len(initial["missions"]) == 20
    inserted = admit(chat_http, "New turn during history pagination", first["sessionId"])
    last = missions(chat_http, first["sessionId"], beforeId=initial["nextBeforeId"]).json()
    found = [value["id"] for value in initial["missions"] + last["missions"]]
    assert found == [value["missionId"] for value in reversed(values)]
    assert inserted["missionId"] not in found
    assert not last["hasMore"] and last["nextBeforeId"] is None


def test_foreign_session_and_unknown_mission_cursors_are_rejected(chat_http):
    first, other = admit(chat_http), admit(chat_http)
    for cursor in (other["missionId"], "missing-mission"):
        response = missions(chat_http, first["sessionId"], beforeId=cursor)
        assert response.status_code == 404
        assert response.json() == {"detail": "Mission history cursor not found"}


def test_non_chat_source_with_same_session_cannot_enter_directory_or_cursor(chat_http):
    first = admit(chat_http)
    issue_id = "mis-history-issue"

    async def seed_issue_mission():
        repository = MissionRepository()
        mission = await repository.get_mission(first["missionId"])
        await repository.add_mission(mission.model_copy(update={
            "id": issue_id,
            "source": MissionSource(type="issue", session_id=first["sessionId"]),
        }))

    chat_http.portal.call(seed_issue_mission)
    page = missions(chat_http, first["sessionId"]).json()
    assert [value["id"] for value in page["missions"]] == [first["missionId"]]
    assert missions(chat_http, first["sessionId"], beforeId=issue_id).status_code == 404


def test_history_reads_are_state_neutral_and_limits_are_validated(chat_http):
    first = admit(chat_http)
    before = chat_http.get(f"/fixture/missions/{first['missionId']}", headers=HUMAN).json()
    directory(chat_http)
    missions(chat_http, first["sessionId"])
    after = chat_http.get(f"/fixture/missions/{first['missionId']}", headers=HUMAN).json()
    assert before == after and after["evidence"] == []
    for parameters in ({"limit": 0}, {"limit": 51}, {"offset": -1}):
        assert directory(chat_http, **parameters).status_code == 422
    for parameters in ({"limit": 0}, {"limit": 21}, {"beforeId": ""}):
        assert missions(chat_http, first["sessionId"], **parameters).status_code == 422
