"""Chronological session pagination over actual mixed-format database rows."""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from app.db.migrations.session_workspace import (
    SESSION_WORKSPACE_REVISION,
    SESSION_WORKSPACE_UPGRADE,
    upgrade_session_workspace_sqlite,
)
from app.db.sqlite_pool import SQLitePool
from app.domain import ActorRef, Session
from app.repositories.session_repository import SessionRepository


@asynccontextmanager
async def _database(tmp_path: Path, backend: str, *, native: bool = False):
    if backend == "sqlite":
        pool = SQLitePool(tmp_path / "session-order.sqlite3")
        await pool.initialize()
        try:
            async with pool.acquire() as connection:
                await connection.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
                await upgrade_session_workspace_sqlite(connection)
                yield connection
        finally:
            await pool.close()
        return
    dsn = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("AGENTHUB_TEST_POSTGRES_DSN required for real PostgreSQL timestamp tests")
    connection = await asyncpg.connect(dsn, server_settings={"timezone": "Asia/Shanghai"})
    schema = "agenthub_timestamp_" + uuid.uuid4().hex
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        await connection.execute(f'SET search_path TO "{schema}"')
        kind = "TIMESTAMPTZ" if native else "TEXT"
        await connection.execute(f"CREATE TABLE sessions(id TEXT PRIMARY KEY, created_at {kind} NOT NULL, updated_at {kind})")
        if native:
            await connection.execute("INSERT INTO sessions VALUES('native-older','2026-09-01T09:00:00Z','2026-09-01T09:00:00Z')")
        for statement in SESSION_WORKSPACE_UPGRADE:
            await connection.execute(statement)
        # Model an already-upgraded profile. Sorting must not depend on replaying
        # or changing the historical c8 migration once these text rows exist.
        await connection.execute("CREATE TABLE alembic_version(version_num TEXT PRIMARY KEY)")
        await connection.execute("INSERT INTO alembic_version VALUES($1)", SESSION_WORKSPACE_REVISION)
        yield connection
    finally:
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await connection.close()


async def _insert_text_rows(connection: Any, rows: list[tuple[str, str]]) -> None:
    for session_id, timestamp in rows:
        await connection.execute("""INSERT INTO sessions(id, workspace_id, title, status,
            created_by_type, created_by_id, created_at, updated_at)
            VALUES($1,'workspace','Mixed times','ACTIVE','human','alice',$2,$2)""", session_id, timestamp)


def _repository(connection: Any, backend: str) -> SessionRepository:
    return SessionRepository(
        execute=connection.execute, fetch_one=connection.fetchrow,
        fetch_all=connection.fetch, backend=backend,
    )


def _created_at(session_id: str, value: datetime) -> Session:
    return Session(id=session_id, workspace_id="workspace", title="New time",
                   created_by=ActorRef(type="human", id="alice"), created_at=value, updated_at=value)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_mixed_separator_offset_and_naive_utc_sort_before_pagination(tmp_path: Path, backend: str) -> None:
    rows = [
        ("space-newest", "2026-09-01 10:30:00+00"),
        ("t-older", "2026-09-01T09:45:00+00:00"),
        ("positive-offset", "2026-09-01T17:00:00+08:00"),
        ("compact-offset", "2026-09-01T15:00:00+0800"),
        ("negative-offset", "2026-09-01T02:30:00-04:00"),
        ("next-day-offset", "2026-09-02T00:00:00+14:00"),
        ("naive-utc", "2026-09-01 09:30:00"),
        ("z-utc", "2026-09-01T09:00:00Z"),
        ("date-only", "2026-09-01"),
    ]
    expected = [name for name, value in sorted(rows, key=lambda item: (
        datetime.fromisoformat(item[1]).replace(tzinfo=UTC) if datetime.fromisoformat(item[1]).tzinfo is None
        else datetime.fromisoformat(item[1]).astimezone(UTC), item[0]), reverse=True)]
    async with _database(tmp_path, backend) as connection:
        await _insert_text_rows(connection, rows)
        repository = _repository(connection, backend)
        found = await repository.list_sessions("workspace")
        assert [session.id for session in found] == expected
        assert all(session.created_at.tzinfo == UTC for session in found)
        pages = [await repository.list_sessions("workspace", limit=3, offset=offset) for offset in (0, 3, 6)]
        assert [session.id for page in pages for session in page] == expected
        assert await repository.list_sessions("other-workspace") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_microseconds_do_not_round_across_seconds_or_use_id_order(tmp_path: Path, backend: str) -> None:
    rows = [
        ("z-earlier", "2026-09-01 09:00:00.000100+00"),
        ("a-later", "2026-09-01T17:00:00.000200+08:00"),
        ("z-before-second", "2026-09-01T09:00:00.999999Z"),
        ("a-next-second", "2026-09-01T09:00:01Z"),
        ("a-one-digit", "2026-09-01T09:00:00.1Z"),
    ]
    async with _database(tmp_path, backend) as connection:
        await _insert_text_rows(connection, rows)
        sessions = await _repository(connection, backend).list_sessions("workspace")
        assert [session.id for session in sessions] == [
            "a-next-second", "z-before-second", "a-one-digit", "a-later", "z-earlier",
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_equal_instants_have_stable_id_order_and_new_writes_use_utc(tmp_path: Path, backend: str) -> None:
    rows = [("a-z", "2026-09-01T09:00:00Z"), ("z-space", "2026-09-01 17:00:00+08")]
    async with _database(tmp_path, backend) as connection:
        await _insert_text_rows(connection, rows)
        repository = _repository(connection, backend)
        assert [value.id for value in await repository.list_sessions("workspace")] == ["z-space", "a-z"]
        value = datetime(2026, 9, 1, 18, 0, 0, 123, tzinfo=timezone(timedelta(hours=8)))
        await repository.add_session(_created_at("new", value))
        row = await connection.fetchrow("SELECT created_at,updated_at FROM sessions WHERE id='new'")
        assert row["created_at"] == row["updated_at"] == "2026-09-01T10:00:00.000123+00:00"
        session = await repository.get_session("new")
        assert session.created_at == value and session.created_at.tzinfo == UTC
        assert (await repository.list_sessions("workspace"))[0].id == "new"


@pytest.mark.asyncio
async def test_native_postgres_migration_remains_correct_with_new_iso_writes(tmp_path: Path) -> None:
    async with _database(tmp_path, "postgresql", native=True) as connection:
        await connection.execute("""UPDATE sessions SET workspace_id='workspace', title='Old',
            status='ACTIVE', created_by_type='human', created_by_id='alice' WHERE id='native-older'""")
        stored = await connection.fetchval("SELECT created_at FROM sessions WHERE id='native-older'")
        assert " " in stored
        repository = _repository(connection, "postgresql")
        await repository.add_session(_created_at("newer", datetime(2026, 9, 1, 10, tzinfo=UTC)))
        assert [session.id for session in await repository.list_sessions("workspace")] == ["newer", "native-older"]
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == SESSION_WORKSPACE_REVISION
