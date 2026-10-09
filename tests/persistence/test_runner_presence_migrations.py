"""Real SQLite version-4 profiles receive only the transactional presence upgrade."""
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
        self.pool = SQLitePool(Path(temporary.name) / "presence-migration.sqlite3")
        await self.pool.initialize()
        self.addAsyncCleanup(self.pool.close)
        await self.initialize()
        async with self.pool.acquire() as connection:
            await connection.execute("DROP TABLE runner_presence")
            await connection.execute("DELETE FROM schema_migrations WHERE version >= 4")
            await connection.execute("INSERT INTO schema_migrations VALUES(4,'original-profile')")
            await connection.execute("UPDATE users SET name='preserved-local-owner',password_hash='preserved-local-hash'")

    async def initialize(self, pool=None):
        with mock.patch("app.db.session.aget_pool", new=mock.AsyncMock(return_value=pool or self.pool)):
            await _ainit_sqlite()

    async def test_upgrade_never_replays_legacy_bootstrap_or_seeds(self):
        with mock.patch("app.db.sqlite_initializer._bootstrap_legacy", side_effect=AssertionError("seeds must not run")):
            await self.initialize()
            await self.initialize()
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("SELECT MAX(version) FROM schema_migrations"), 5)
            self.assertEqual(await connection.fetchval("SELECT COUNT(*) FROM schema_migrations WHERE version=5"), 1)
            self.assertEqual(await connection.fetchval("SELECT COUNT(*) FROM runner_presence"), 0)
            owners = await connection.fetch("SELECT name,password_hash FROM users")
            self.assertTrue(owners)
            self.assertTrue(all(row == {"name": "preserved-local-owner", "password_hash": "preserved-local-hash"} for row in owners))

    async def test_failed_upgrade_rolls_back_table_and_version_marker(self):
        class FailingConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            async def execute(self, statement, *args):
                if "CREATE INDEX IF NOT EXISTS idx_runner_presence_binding" in statement:
                    raise RuntimeError("injected presence migration failure")
                return await self.connection.execute(statement, *args)

        class FailingPool:
            @asynccontextmanager
            async def acquire(inner_self):
                async with self.pool.acquire() as connection:
                    yield FailingConnection(connection)

        with self.assertRaisesRegex(RuntimeError, "injected presence migration failure"):
            await self.initialize(FailingPool())
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("SELECT MAX(version) FROM schema_migrations"), 4)
            self.assertEqual(await connection.fetchval("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='runner_presence'"), 0)
            self.assertEqual(await connection.fetchval("SELECT COUNT(*) FROM users WHERE password_hash='preserved-local-hash'"), 1)
        await self.initialize()
        async with self.pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("SELECT MAX(version) FROM schema_migrations"), 5)
