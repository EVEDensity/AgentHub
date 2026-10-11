"""Version-4 to version-5 local upgrades include observation DDL and marker."""
from __future__ import annotations

import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest import mock

from app.db.init_db import _ainit_sqlite
from app.db.sqlite_pool import SQLitePool


class RunnerPresenceSQLiteMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.pool = SQLitePool(Path(temporary.name) / "presence.sqlite3")
        await self.pool.initialize()
        self.addAsyncCleanup(self.pool.close)
        await self._initialize()
        async with self.pool.acquire() as connection:
            await connection.execute("DROP TABLE runner_presence")
            await connection.execute("DELETE FROM schema_migrations")
            await connection.execute("INSERT INTO schema_migrations VALUES(4,'version-4-profile')")
            await connection.execute("CREATE TABLE user_notes(value TEXT)")
            await connection.execute("INSERT INTO user_notes VALUES('keep my data')")

    async def _initialize(self, pool=None):
        with mock.patch("app.db.session.aget_pool", new=mock.AsyncMock(return_value=pool or self.pool)):
            await _ainit_sqlite()

    async def test_incremental_upgrade_is_idempotent_and_preserves_user_data(self):
        await self._initialize()
        await self._initialize()
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("SELECT MAX(version) FROM schema_migrations"), 5)
            self.assertEqual(await connection.fetchval("SELECT value FROM user_notes"), "keep my data")
            self.assertEqual(await connection.fetchval("SELECT COUNT(*) FROM runner_presence"), 0)

    async def test_index_failure_rolls_back_table_and_marker(self):
        class FailingConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            async def execute(self, statement, *args):
                if "CREATE INDEX IF NOT EXISTS idx_runner_presence_binding" in statement:
                    raise RuntimeError("presence index unavailable")
                return await self.connection.execute(statement, *args)

        class FailingPool:
            @asynccontextmanager
            async def acquire(inner_self):
                async with self.pool.acquire() as connection:
                    yield FailingConnection(connection)

        with self.assertRaisesRegex(RuntimeError, "presence index unavailable"):
            await self._initialize(FailingPool())
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("SELECT MAX(version) FROM schema_migrations"), 4)
            self.assertEqual(await connection.fetchval("SELECT COUNT(*) FROM sqlite_master WHERE name='runner_presence'"), 0)
            self.assertEqual(await connection.fetchval("SELECT value FROM user_notes"), "keep my data")
