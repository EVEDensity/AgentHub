"""FastAPI authorization and cursor tests backed by the real SQLite boot path.

Only authenticated identity is supplied by a test dependency. Session and
event reads/writes use the production repositories and initialized database.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from app.api.v1.sessions import router
from app.db.init_db import _ainit_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain import ActorRef, Session, SessionEvent, SessionEventType
from app.repositories import SessionEventRepository, SessionRepository
from app.services.auth_service import get_current_user

_NOW = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)


@pytest.fixture
def session_client(tmp_path, monkeypatch):
    import app.db.session as database

    pool = SQLitePool(tmp_path / "sessions.sqlite3")

    async def get_pool():
        return pool

    monkeypatch.setattr(database, "aget_pool", get_pool)

    async def initialize():
        await pool.initialize()
        await _ainit_sqlite()
        for name in ("alice", "bob"):
            await database.aexecute(
                "INSERT INTO users(id,name,role,created_at) VALUES($1,$1,'user',$2)",
                name, _NOW.isoformat(),
            )
            await SessionRepository().add_session(Session(
                id=f"session-{name}", workspace_id=name, title=f"{name} private session",
                created_by=ActorRef(type="human", id=name),
                created_at=_NOW, updated_at=_NOW,
            ))
            await SessionEventRepository().add_session_event(SessionEvent(
                id=f"{name}-event-0000", session_id=f"session-{name}",
                event_type=SessionEventType.MESSAGE_CREATED,
                actor=ActorRef(type="human", id=name),
                payload={"secret": f"{name} private message"}, created_at=_NOW,
            ))
        await database.aexecute(
            """INSERT INTO sessions(id,name,created_at,owner_id)
                VALUES('legacy-alice','Unassigned legacy scope',$1,'alice')""",
            _NOW.isoformat(),
        )
        await SessionEventRepository().add_session_event(SessionEvent(
            id="legacy-alice-event", session_id="legacy-alice",
            event_type=SessionEventType.MESSAGE_CREATED,
            actor=ActorRef(type="human", id="alice"),
            payload={"secret": "legacy private message"}, created_at=_NOW,
        ))

    try:
        asyncio.run(initialize())
    except BaseException:
        asyncio.run(pool.close())
        raise

    def authenticated_user(x_test_user: str | None = Header(default=None)):
        if x_test_user not in {"alice", "bob"}:
            raise HTTPException(status_code=401, detail="Authentication required")
        return {"id": x_test_user, "name": x_test_user, "role": "user"}

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = authenticated_user
    try:
        with TestClient(app) as client:
            yield client
    finally:
        asyncio.run(pool.close())


def _event_url(session_id, *, stream=False):
    return f"/api/v1/sessions/{session_id}/events" + ("/stream" if stream else "")


def _parameters(workspace, *, stream=False, **extra):
    parameters = {"workspaceId": workspace}
    if stream:
        parameters.update(maxSeconds=0.15, pollSeconds=0.11)
    parameters.update(extra)
    return parameters


def _frames(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("user", ["alice", "bob"])
def test_ordinary_users_can_read_their_persisted_session(session_client, stream, user):
    response = session_client.get(
        _event_url(f"session-{user}", stream=stream),
        params=_parameters(user, stream=stream), headers={"X-Test-User": user},
    )
    assert response.status_code == 200, response.text
    events = _frames(response) if stream else response.json()["events"]
    assert [event["id"] for event in events] == [f"{user}-event-0000"]
    assert events[0]["payload"] == {"secret": f"{user} private message"}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("user,other", [("alice", "bob"), ("bob", "alice")])
def test_client_workspace_cannot_relabel_another_users_session(session_client, stream, user, other):
    response = session_client.get(
        _event_url(f"session-{other}", stream=stream),
        params=_parameters(user, stream=stream), headers={"X-Test-User": user},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}
    assert "private message" not in response.text


@pytest.mark.parametrize("stream", [False, True])
def test_unauthorized_requested_workspace_is_rejected(session_client, stream):
    response = session_client.get(
        _event_url("session-bob", stream=stream),
        params=_parameters("bob", stream=stream), headers={"X-Test-User": "alice"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("stream", [False, True])
def test_legacy_owner_does_not_invent_v1_workspace_scope(session_client, stream):
    response = session_client.get(
        _event_url("legacy-alice", stream=stream),
        params=_parameters("alice", stream=stream), headers={"X-Test-User": "alice"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}
    assert "legacy private message" not in response.text


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("cursor", ["bob-event-0000", "missing-event"])
def test_unknown_and_foreign_session_cursors_are_rejected_before_streaming(session_client, stream, cursor):
    response = session_client.get(
        _event_url("session-alice", stream=stream),
        params=_parameters("alice", stream=stream, afterId=cursor),
        headers={"X-Test-User": "alice"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Session event cursor not found"}


@pytest.mark.parametrize("stream", [False, True])
def test_missing_session_and_authentication_are_rejected(session_client, stream):
    response = session_client.get(
        _event_url("missing-session", stream=stream),
        params=_parameters("alice", stream=stream), headers={"X-Test-User": "alice"},
    )
    assert response.status_code == 404
    response = session_client.get(
        _event_url("session-alice", stream=stream), params=_parameters("alice", stream=stream),
    )
    assert response.status_code == 401


def _append_history():
    async def append():
        repo = SessionEventRepository()
        for index in range(1, 601):
            await repo.add_session_event(SessionEvent(
                id=f"alice-event-{index:04d}", session_id="session-alice",
                event_type=(SessionEventType.MESSAGE_CREATED if index % 2 == 0 else SessionEventType.MISSION_CREATED),
                actor=ActorRef(type="human", id="alice"),
                payload={"index": index}, created_at=_NOW,
            ))
    asyncio.run(append())


def test_keyset_pages_cover_601_equal_timestamp_events_without_replay(session_client):
    _append_history()
    ids = []
    cursor = None
    while True:
        parameters = _parameters("alice", limit=200)
        if cursor:
            parameters["afterId"] = cursor
        response = session_client.get(
            _event_url("session-alice"), params=parameters, headers={"X-Test-User": "alice"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 601
        ids.extend(event["id"] for event in body["events"])
        if not body["hasMore"]:
            break
        assert body["nextAfterId"] != cursor
        cursor = body["nextAfterId"]
    assert ids == [f"alice-event-{index:04d}" for index in range(601)]


def test_filtered_cursor_after_500_does_not_require_cursor_type_to_match(session_client):
    _append_history()
    response = session_client.get(
        _event_url("session-alice"),
        params=_parameters("alice", afterId="alice-event-0551", eventType="message.created", limit=15),
        headers={"X-Test-User": "alice"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [event["id"] for event in body["events"]] == [f"alice-event-{index:04d}" for index in range(552, 582, 2)]
    assert body["total"] == 301
    assert body["hasMore"] is True


def test_sse_drains_more_than_one_page_and_reconnects_after_event_550(session_client):
    _append_history()
    response = session_client.get(
        _event_url("session-alice", stream=True),
        params=_parameters("alice", stream=True, maxSeconds=2), headers={"X-Test-User": "alice"},
    )
    assert response.status_code == 200
    assert [event["id"] for event in _frames(response)] == [f"alice-event-{index:04d}" for index in range(601)]
    resumed = session_client.get(
        _event_url("session-alice", stream=True),
        params=_parameters("alice", stream=True, afterId="alice-event-0550", maxSeconds=2),
        headers={"X-Test-User": "alice"},
    )
    assert resumed.status_code == 200
    assert [event["id"] for event in _frames(resumed)] == [f"alice-event-{index:04d}" for index in range(551, 601)]


@pytest.mark.parametrize("stream", [False, True])
def test_invalid_event_type_uses_the_documented_camelcase_parameter(session_client, stream):
    response = session_client.get(
        _event_url("session-alice", stream=stream),
        params=_parameters("alice", stream=stream, eventType="unknown"),
        headers={"X-Test-User": "alice"},
    )
    assert response.status_code == 422
