"""Actual SQLite/PostgreSQL chat source filtering and chronological keysets."""
from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import asyncpg
import pytest

from app.db.sqlite_pool import SQLitePool
from app.repositories.session_mission_history import (
    SessionMissionCursorNotFound,
    SessionMissionHistoryRepository,
)


def utc(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


@asynccontextmanager
async def database(tmp_path, backend):
    if backend == "sqlite":
        pool = SQLitePool(tmp_path / "mission-history.sqlite3")
        await pool.initialize()
        try:
            async with pool.acquire() as connection:
                await connection.execute("""CREATE TABLE missions(
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL,
                    objective TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL)""")
                yield connection
        finally:
            await pool.close()
        return
    dsn = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("AGENTHUB_TEST_POSTGRES_DSN required for actual PostgreSQL Mission history")
    connection = await asyncpg.connect(dsn, server_settings={"timezone": "Asia/Shanghai"})
    schema = "agenthub_history_" + uuid.uuid4().hex
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        await connection.execute(f'SET search_path TO "{schema}"')
        await connection.execute("""CREATE TABLE missions(
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL,
            objective TEXT NOT NULL, source JSONB NOT NULL, status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL)""")
        yield connection
    finally:
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await connection.close()


async def insert(connection, backend, identifier, timestamp, *, workspace="workspace", source=None):
    source = source if source is not None else {"type": "chat", "sessionId": "session"}
    await connection.execute("""INSERT INTO missions(
        id,workspace_id,title,objective,source,status,created_at)
        VALUES($1,$2,'History target','Bounded goal',$3,'RUNNING',$4)""",
        identifier, workspace, json.dumps(source), timestamp if backend == "sqlite" else utc(timestamp))


def repository(connection, backend):
    return SessionMissionHistoryRepository(
        fetch_one=connection.fetchrow, fetch_all=connection.fetch, backend=backend,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_mixed_timezone_microsecond_and_equal_instant_keysets(tmp_path, backend):
    rows = [
        ("space-newest", "2026-09-01 10:30:00+00"),
        ("next-day-offset", "2026-09-02T00:00:00+14:00"),
        ("naive-utc", "2026-09-01 09:30:00"),
        ("z-before-second", "2026-09-01T09:00:00.999999Z"),
        ("a-next-second", "2026-09-01T09:00:01Z"),
        ("a-one-digit", "2026-09-01T09:00:00.1Z"),
        ("z-earlier", "2026-09-01 09:00:00.000100+00"),
        ("a-later", "2026-09-01T17:00:00.000200+08:00"),
        ("a-same-z", "2026-09-01T09:00:00Z"),
        ("z-same-space", "2026-09-01 17:00:00+08"),
        ("compact-offset", "2026-09-01T15:00:00+0800"),
        ("negative-offset", "2026-09-01T02:30:00-04:00"),
        ("date-only", "2026-09-01"),
    ]
    expected = [identifier for identifier, value in sorted(
        rows, key=lambda item: (utc(item[1]), item[0]), reverse=True)]
    async with database(tmp_path, backend) as connection:
        for identifier, timestamp in rows:
            await insert(connection, backend, identifier, timestamp)
        history = repository(connection, backend)
        found = await history.list_for_session("workspace", "session", limit=21)
        assert [value.id for value in found] == expected
        assert all(value.created_at.tzinfo == UTC for value in found)
        # A one-row page exercises every individual timestamp and ID boundary,
        # including .999999 before the next second and equal UTC instants.
        identifiers, cursor = [], None
        for _ in rows:
            page = await history.list_for_session("workspace", "session", limit=1, before_id=cursor)
            assert len(page) == 1
            cursor = page[0].id
            identifiers.append(cursor)
        assert identifiers == expected
        assert await history.list_for_session("workspace", "session", limit=1, before_id=cursor) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_source_and_workspace_scope_apply_to_rows_and_cursor(tmp_path, backend):
    async with database(tmp_path, backend) as connection:
        timestamp = "2026-09-01T09:00:00Z"
        await insert(connection, backend, "native", timestamp)
        for identifier, workspace, source in (
            ("foreign-workspace", "other", {"type": "chat", "sessionId": "session"}),
            ("foreign-session", "workspace", {"type": "chat", "sessionId": "other"}),
            ("issue-source", "workspace", {"type": "issue", "sessionId": "session"}),
            ("missing-session", "workspace", {"type": "chat"}),
            ("null-session", "workspace", {"type": "chat", "sessionId": None}),
        ):
            await insert(connection, backend, identifier, timestamp, workspace=workspace, source=source)
        history = repository(connection, backend)
        assert [value.id for value in await history.list_for_session("workspace", "session", limit=21)] == ["native"]
        for cursor in ("foreign-workspace", "foreign-session", "issue-source", "missing-session", "null-session", "unknown"):
            with pytest.raises(SessionMissionCursorNotFound):
                await history.list_for_session("workspace", "session", limit=1, before_id=cursor)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_newer_insert_does_not_shift_older_keyset_page(tmp_path, backend):
    async with database(tmp_path, backend) as connection:
        for identifier, timestamp in (("older", "2026-09-01T09:00:00Z"),
                                      ("newer", "2026-09-01T09:00:00.000001Z")):
            await insert(connection, backend, identifier, timestamp)
        history = repository(connection, backend)
        first = await history.list_for_session("workspace", "session", limit=1)
        assert first[0].id == "newer"
        await insert(connection, backend, "inserted", "2026-09-01T09:00:01Z")
        remaining = await history.list_for_session("workspace", "session", limit=21, before_id=first[0].id)
        assert [value.id for value in remaining] == ["older"]
        assert (await history.list_for_session("workspace", "session", limit=1))[0].id == "inserted"
