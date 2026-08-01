from __future__ import annotations

import unittest
from typing import Any

from app.db.migrations import (
    MISSION_CONTROL_PLANE_DOWN_REVISION,
    MISSION_CONTROL_PLANE_REVISION,
    MISSION_CONTROL_PLANE_UPGRADE,
    MISSION_EVENT_LEDGER_REVISION,
    MISSION_EVENT_LEDGER_UPGRADE,
)
from app.db.migrations.runner import (
    UnsupportedMigrationPath,
    apply_startup_migrations,
)


class FakeConnection:
    def __init__(
        self,
        current_revision: str | None,
        *,
        fail_on: str | None = None,
    ) -> None:
        self.current_revision = current_revision
        self.fail_on = fail_on
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> None:
        self.executed.append((sql, args))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("injected migration failure")
        if sql.startswith(("INSERT INTO alembic_version", "UPDATE alembic_version")):
            self.current_revision = args[0]

    async def fetchrow(self, _sql: str) -> dict[str, str] | None:
        if self.current_revision is None:
            return None
        return {"version_num": self.current_revision}


class StartupMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_database_creates_tables_before_stamping_head(self) -> None:
        connection = FakeConnection(None)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in MISSION_CONTROL_PLANE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in MISSION_EVENT_LEDGER_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(connection.current_revision, MISSION_EVENT_LEDGER_REVISION)
        self.assertTrue(statements[-1].startswith("INSERT INTO alembic_version"))

    async def test_previous_head_is_upgraded_and_versioned_last(self) -> None:
        connection = FakeConnection(MISSION_CONTROL_PLANE_DOWN_REVISION)

        await apply_startup_migrations(connection)

        self.assertEqual(connection.current_revision, MISSION_EVENT_LEDGER_REVISION)
        self.assertTrue(connection.executed[-1][0].startswith("UPDATE alembic_version"))

    async def test_current_head_is_idempotent(self) -> None:
        connection = FakeConnection(MISSION_EVENT_LEDGER_REVISION)

        await apply_startup_migrations(connection)

        self.assertEqual(len(connection.executed), 1)

    async def test_mission_control_plane_head_advances_to_event_ledger(self) -> None:
        connection = FakeConnection(MISSION_CONTROL_PLANE_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in MISSION_EVENT_LEDGER_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(connection.current_revision, MISSION_EVENT_LEDGER_REVISION)

    async def test_unknown_upgrade_path_is_not_falsely_stamped(self) -> None:
        connection = FakeConnection("unknown-revision")

        with (
            self.assertLogs("agenthub.db.migrations", level="ERROR"),
            self.assertRaisesRegex(
                UnsupportedMigrationPath, "unsupported Alembic upgrade path"
            ),
        ):
            await apply_startup_migrations(connection)

        self.assertEqual(connection.current_revision, "unknown-revision")
        self.assertEqual(len(connection.executed), 1)

    async def test_failed_migration_does_not_advance_revision(self) -> None:
        connection = FakeConnection(
            MISSION_CONTROL_PLANE_DOWN_REVISION,
            fail_on="CREATE TABLE IF NOT EXISTS missions",
        )

        with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
            await apply_startup_migrations(connection)

        self.assertEqual(
            connection.current_revision, MISSION_CONTROL_PLANE_DOWN_REVISION
        )


if __name__ == "__main__":
    unittest.main()
