from __future__ import annotations

import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest import mock

from app.db.init_db import _ainit_sqlite, SQLITE_SCHEMA_VERSION
from app.db.sqlite_pool import SQLitePool

RESUME_COLUMNS = {
    "resume_protocol_version",
    "next_action",
    "idempotency_key",
    "workspace_revision",
    "context_manifest_digest",
}


class CheckpointResumeSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.pool = SQLitePool(Path(temporary.name) / "missions.sqlite3")
        await self.pool.initialize()
        self.addAsyncCleanup(self.pool.close)

    async def _legacy_database(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, name TEXT NOT NULL)")
            await connection.execute("INSERT INTO sessions VALUES('old-session', '2026-09-01', 'Legacy session')")
            await connection.execute(
                "INSERT INTO schema_migrations VALUES(2, '2026-09-01')"
            )
            await connection.execute(
                "CREATE TABLE user_notes(id TEXT PRIMARY KEY, content TEXT)"
            )
            await connection.execute(
                "INSERT INTO user_notes VALUES('mine', 'preserve this')"
            )
            await connection.execute("""CREATE TABLE execution_checkpoints(
                id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, work_unit_id TEXT NOT NULL,
                attempt INTEGER NOT NULL, sequence INTEGER NOT NULL, phase TEXT NOT NULL,
                iteration INTEGER NOT NULL, tool_calls INTEGER NOT NULL,
                prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
                model_cost REAL NOT NULL, terminal INTEGER NOT NULL, failure_reason TEXT,
                state_digest TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL
            )""")
            await connection.execute(
                """INSERT INTO execution_checkpoints VALUES(
                'old-checkpoint', 'mis-1', 'wu-1', 1, 1, 'harness.execution.started',
                0, 0, 12, 4, 0.25, 0, NULL, $1, $2, '2026-09-01T00:00:00Z'
            )""",
                "sha256:" + "a" * 64,
                '{"type":"runner","id":"runner-1"}',
            )

    async def _initialize(self, pool=None) -> None:
        with mock.patch(
            "app.db.session.aget_pool",
            new=mock.AsyncMock(return_value=pool or self.pool),
        ):
            await _ainit_sqlite()

    async def _columns(self) -> set[str]:
        async with self.pool.acquire() as connection:
            return {
                row["name"]
                for row in await connection.fetch(
                    "PRAGMA table_info(execution_checkpoints)"
                )
            }

    async def test_version_two_upgrade_preserves_checkpoint_and_user_data(self) -> None:
        await self._legacy_database()
        await self._initialize()
        self.assertTrue(RESUME_COLUMNS <= await self._columns())
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM execution_checkpoints WHERE id='old-checkpoint'"
            )
            self.assertEqual(row["prompt_tokens"], 12)
            self.assertEqual(row["model_cost"], 0.25)
            self.assertTrue(all(row[column] is None for column in RESUME_COLUMNS))
            self.assertEqual(
                await connection.fetchval("SELECT content FROM user_notes"),
                "preserve this",
            )
            self.assertEqual(
                await connection.fetchval("SELECT MAX(version) FROM schema_migrations"),
                SQLITE_SCHEMA_VERSION,
            )

    async def test_partial_previous_upgrade_and_repeated_boot_are_idempotent(
        self,
    ) -> None:
        await self._legacy_database()
        async with self.pool.acquire() as connection:
            await connection.execute(
                "ALTER TABLE execution_checkpoints ADD COLUMN workspace_revision TEXT"
            )
            await connection.execute(
                "UPDATE execution_checkpoints SET workspace_revision='existing-revision'"
            )
        await self._initialize()
        await self._initialize()
        self.assertTrue(RESUME_COLUMNS <= await self._columns())
        async with self.pool.acquire() as connection:
            self.assertEqual(
                await connection.fetchval(
                    "SELECT workspace_revision FROM execution_checkpoints"
                ),
                "existing-revision",
            )
            self.assertEqual(
                await connection.fetchval(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version=$1", SQLITE_SCHEMA_VERSION
                ),
                1,
            )

    async def test_failure_rolls_back_columns_and_keeps_old_schema_marker(self) -> None:
        await self._legacy_database()
        before = await self._columns()

        class FailingConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            async def execute(self, statement, *args):
                if "ADD COLUMN next_action" in statement:
                    raise RuntimeError("injected resume migration failure")
                return await self.connection.execute(statement, *args)

        class FailingPool:
            @asynccontextmanager
            async def acquire(inner_self):
                async with self.pool.acquire() as connection:
                    yield FailingConnection(connection)

        with self.assertRaisesRegex(RuntimeError, "injected resume migration failure"):
            await self._initialize(FailingPool())
        self.assertEqual(await self._columns(), before)
        async with self.pool.acquire() as connection:
            self.assertEqual(
                await connection.fetchval("SELECT MAX(version) FROM schema_migrations"),
                2,
            )

    async def test_missing_checkpoint_table_fails_without_stamping_ready(self) -> None:
        await self._legacy_database()
        async with self.pool.acquire() as connection:
            await connection.execute("DROP TABLE execution_checkpoints")
        with self.assertRaisesRegex(RuntimeError, "execution_checkpoints"):
            await self._initialize()
        async with self.pool.acquire() as connection:
            self.assertEqual(
                await connection.fetchval("SELECT MAX(version) FROM schema_migrations"),
                2,
            )

    async def test_fresh_profile_installs_current_checkpoint_columns(self) -> None:
        await self._initialize()
        self.assertTrue(RESUME_COLUMNS <= await self._columns())
        async with self.pool.acquire() as connection:
            self.assertEqual(
                await connection.fetchval("SELECT MAX(version) FROM schema_migrations"),
                SQLITE_SCHEMA_VERSION,
            )
