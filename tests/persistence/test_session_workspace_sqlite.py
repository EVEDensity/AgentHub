"""Real local-profile upgrades preserve legacy rows and explicit scope."""
from __future__ import annotations

import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from app.db.init_db import _ainit_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain import ActorRef, Session
from app.repositories import SessionRepository


class SessionWorkspaceSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.pool = SQLitePool(Path(temporary.name) / "sessions.sqlite3")
        await self.pool.initialize()
        self.addAsyncCleanup(self.pool.close)

    async def _initialize(self, pool=None):
        with mock.patch("app.db.session.aget_pool", new=mock.AsyncMock(return_value=pool or self.pool)):
            await _ainit_sqlite()

    async def _legacy_profile(self):
        async with self.pool.acquire() as connection:
            await connection.execute("INSERT INTO schema_migrations VALUES(3,'old-version')")
            await connection.execute("""CREATE TABLE sessions(
                id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'group',
                participants TEXT NOT NULL DEFAULT '[]', active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT '',
                visibility TEXT NOT NULL DEFAULT 'private'
            )""")
            await connection.execute("""INSERT INTO sessions(id,name,participants,created_at,owner_id)
                VALUES('legacy','Keep my conversation','["alice"]','2026-09-01','alice')""")

    async def _repository(self):
        async with self.pool.acquire() as connection:
            return SessionRepository(execute=connection.execute, fetch_one=connection.fetchrow, fetch_all=connection.fetch)

    def _session(self, session_id="new-session"):
        now = datetime.now(UTC)
        return Session(id=session_id, workspace_id="alice", title="New scoped session",
                       created_by=ActorRef(type="human", id="alice", display_name=""), created_at=now, updated_at=now)

    async def test_fresh_production_boot_writes_and_reads_scoped_sessions(self):
        await self._initialize()
        repository = await self._repository()
        session = self._session()
        await repository.add_session(session)
        self.assertEqual(await repository.get_session(session.id), session)
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("SELECT MAX(version) FROM schema_migrations"), 4)
            row = await connection.fetchrow("SELECT name,owner_id,workspace_id FROM sessions WHERE id=$1", session.id)
            self.assertEqual(row, {"name": session.title, "owner_id": "alice", "workspace_id": "alice"})

    async def test_version_three_upgrade_preserves_legacy_and_never_guesses_scope(self):
        await self._legacy_profile()
        await self._initialize()
        await self._initialize()
        repository = await self._repository()
        self.assertIsNone(await repository.get_session("legacy"))
        self.assertEqual(await repository.list_sessions("alice"), [])
        async with self.pool.acquire() as connection:
            legacy = await connection.fetchrow("SELECT name,participants,owner_id,workspace_id FROM sessions WHERE id='legacy'")
            self.assertEqual(legacy, {"name": "Keep my conversation", "participants": '["alice"]', "owner_id": "alice", "workspace_id": None})
            self.assertEqual(await connection.fetchval("SELECT COUNT(*) FROM schema_migrations WHERE version=4"), 1)
        await repository.add_session(self._session())
        self.assertIsNotNone(await repository.get_session("new-session"))

    async def test_failed_additive_upgrade_rolls_back_and_keeps_version_three(self):
        await self._legacy_profile()
        async with self.pool.acquire() as connection:
            before = await connection.fetch("PRAGMA table_info(sessions)")

        class FailingConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            async def execute(self, statement, *args):
                if "ADD COLUMN title" in statement:
                    raise RuntimeError("injected session migration failure")
                return await self.connection.execute(statement, *args)

        class FailingPool:
            @asynccontextmanager
            async def acquire(inner_self):
                async with self.pool.acquire() as connection:
                    yield FailingConnection(connection)

        with self.assertRaisesRegex(RuntimeError, "injected session migration failure"):
            await self._initialize(FailingPool())
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetch("PRAGMA table_info(sessions)"), before)
            self.assertEqual(await connection.fetchval("SELECT MAX(version) FROM schema_migrations"), 3)
            self.assertEqual(await connection.fetchval("SELECT name FROM sessions WHERE id='legacy'"), "Keep my conversation")

    async def test_missing_sessions_table_fails_without_advancing_version(self):
        async with self.pool.acquire() as connection:
            await connection.execute("INSERT INTO schema_migrations VALUES(3,'old-version')")
        with self.assertRaisesRegex(RuntimeError, "sessions table is missing"):
            await self._initialize()
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("SELECT MAX(version) FROM schema_migrations"), 3)

    async def test_archiving_preserves_workspace_and_updates_legacy_projection(self):
        await self._legacy_profile()
        await self._initialize()
        repository = await self._repository()
        await repository.add_session(self._session())
        archived = await repository.archive_session("new-session")
        self.assertEqual(archived.status.value, "ARCHIVED")
        self.assertEqual(archived.workspace_id, "alice")
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("SELECT active FROM sessions WHERE id='new-session'"), 0)
