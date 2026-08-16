from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from app.db.migrations import (
    A2A_INBOUND_SOURCE_MAPPING_DOWN_REVISION,
    A2A_INBOUND_SOURCE_MAPPING_UPGRADE,
    A2A_SOURCE_MAPPING_UPGRADE,
    AGENT_BINDING_PERSISTENCE_DOWN_REVISION,
    AGENT_BINDING_PERSISTENCE_UPGRADE,
    AGENT_CATALOG_PROJECTION_DOWN_REVISION,
    AGENT_CATALOG_PROJECTION_UPGRADE,
    ARTIFACT_PERSISTENCE_DOWN_REVISION,
    ARTIFACT_PERSISTENCE_UPGRADE,
    ARTIFACT_TABLE_OWNERSHIP_DOWN_REVISION,
    ARTIFACT_TABLE_OWNERSHIP_REVISION,
    ARTIFACT_TABLE_OWNERSHIP_UPGRADE,
    DECISION_EXPIRY_UPGRADE,
    DECISION_PERSISTENCE_DOWN_REVISION,
    DECISION_PERSISTENCE_REVISION,
    DECISION_PERSISTENCE_UPGRADE,
    DELEGATION_PERSISTENCE_UPGRADE,
    EVIDENCE_PROJECTION_DOWN_REVISION,
    EVIDENCE_PROJECTION_REVISION,
    EVIDENCE_PROJECTION_UPGRADE,
    MISSION_CONTROL_PLANE_DOWN_REVISION,
    MISSION_CONTROL_PLANE_REVISION,
    MISSION_CONTROL_PLANE_UPGRADE,
    MISSION_EVENT_LEDGER_REVISION,
    MISSION_EVENT_LEDGER_UPGRADE,
    WORK_UNIT_PERSISTENCE_REVISION,
    WORK_UNIT_PERSISTENCE_UPGRADE,
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
        for statement in WORK_UNIT_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in A2A_SOURCE_MAPPING_UPGRADE:
            self.assertIn(statement, statements)
        for statement in ARTIFACT_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in EVIDENCE_PROJECTION_UPGRADE:
            self.assertIn(statement, statements)
        for statement in DELEGATION_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in AGENT_BINDING_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in AGENT_CATALOG_PROJECTION_UPGRADE:
            self.assertIn(statement, statements)
        for statement in A2A_INBOUND_SOURCE_MAPPING_UPGRADE:
            self.assertIn(statement, statements)
        for statement in DECISION_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in DECISION_EXPIRY_UPGRADE:
            self.assertIn(statement, statements)
        for statement in ARTIFACT_TABLE_OWNERSHIP_UPGRADE:
            self.assertIn(statement, statements)
        expiry_sql = "\n".join(DECISION_EXPIRY_UPGRADE)
        self.assertIn("'EXPIRED'", expiry_sql)
        self.assertIn("decisions_lifecycle_check", expiry_sql)
        self.assertIn("idx_decisions_pending_expiry", expiry_sql)
        self.assertIn("WHERE status = 'PENDING' AND expires_at IS NOT NULL", expiry_sql)
        self.assertEqual(
            connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION
        )
        self.assertTrue(statements[-1].startswith("INSERT INTO alembic_version"))

    async def test_previous_head_is_upgraded_and_versioned_last(self) -> None:
        connection = FakeConnection(MISSION_CONTROL_PLANE_DOWN_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in MISSION_CONTROL_PLANE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in MISSION_EVENT_LEDGER_UPGRADE:
            self.assertIn(statement, statements)
        for statement in WORK_UNIT_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in A2A_SOURCE_MAPPING_UPGRADE:
            self.assertIn(statement, statements)
        for statement in ARTIFACT_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in EVIDENCE_PROJECTION_UPGRADE:
            self.assertIn(statement, statements)
        for statement in DELEGATION_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in AGENT_BINDING_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in AGENT_CATALOG_PROJECTION_UPGRADE:
            self.assertIn(statement, statements)
        for statement in A2A_INBOUND_SOURCE_MAPPING_UPGRADE:
            self.assertIn(statement, statements)
        for statement in DECISION_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in DECISION_EXPIRY_UPGRADE:
            self.assertIn(statement, statements)
        for statement in ARTIFACT_TABLE_OWNERSHIP_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(
            connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION
        )
        self.assertTrue(connection.executed[-1][0].startswith("UPDATE alembic_version"))

    async def test_current_head_is_idempotent(self) -> None:
        connection = FakeConnection(ARTIFACT_TABLE_OWNERSHIP_REVISION)

        await apply_startup_migrations(connection)

        self.assertEqual(len(connection.executed), 1)

    async def test_decision_persistence_head_advances_only_expiry(self) -> None:
        connection = FakeConnection(DECISION_PERSISTENCE_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in DECISION_PERSISTENCE_UPGRADE:
            self.assertNotIn(statement, statements)
        for statement in DECISION_EXPIRY_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(
            connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION
        )

    async def test_decision_expiry_head_advances_only_artifact_ownership(
        self,
    ) -> None:
        connection = FakeConnection(ARTIFACT_TABLE_OWNERSHIP_DOWN_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in DECISION_EXPIRY_UPGRADE:
            self.assertNotIn(statement, statements)
        for statement in ARTIFACT_TABLE_OWNERSHIP_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(
            connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION
        )

    def test_legacy_and_mission_artifacts_have_distinct_tables(self) -> None:
        initial_migration = Path(
            "migrations/versions/ff209a40779d_initial_schema.py"
        ).read_text(encoding="utf-8")
        mission_sql = "\n".join(ARTIFACT_PERSISTENCE_UPGRADE)

        self.assertIn("CREATE TABLE IF NOT EXISTS artifacts (", initial_migration)
        self.assertIn("session_id TEXT NOT NULL", initial_migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS mission_artifacts (", mission_sql)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS artifacts (", mission_sql)
        ownership_sql = "\n".join(ARTIFACT_TABLE_OWNERSHIP_UPGRADE)
        self.assertIn(
            "RENAME CONSTRAINT artifacts_pkey TO mission_artifacts_pkey",
            ownership_sql,
        )

    async def test_mission_control_plane_head_advances_to_event_ledger(self) -> None:
        connection = FakeConnection(MISSION_CONTROL_PLANE_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in MISSION_EVENT_LEDGER_UPGRADE:
            self.assertIn(statement, statements)
        for statement in WORK_UNIT_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in A2A_SOURCE_MAPPING_UPGRADE:
            self.assertIn(statement, statements)
        for statement in ARTIFACT_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in EVIDENCE_PROJECTION_UPGRADE:
            self.assertIn(statement, statements)
        for statement in DELEGATION_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in AGENT_BINDING_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(
            connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION
        )

    async def test_event_ledger_head_advances_to_work_unit_persistence(self) -> None:
        connection = FakeConnection(MISSION_EVENT_LEDGER_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in WORK_UNIT_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in A2A_SOURCE_MAPPING_UPGRADE:
            self.assertIn(statement, statements)
        for statement in ARTIFACT_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in EVIDENCE_PROJECTION_UPGRADE:
            self.assertIn(statement, statements)
        for statement in MISSION_EVENT_LEDGER_UPGRADE:
            self.assertNotIn(statement, statements)
        for statement in DELEGATION_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in AGENT_BINDING_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION)

    async def test_work_unit_head_advances_through_all_later_revisions(self) -> None:
        connection = FakeConnection(WORK_UNIT_PERSISTENCE_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in A2A_SOURCE_MAPPING_UPGRADE:
            self.assertIn(statement, statements)
        for statement in ARTIFACT_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in EVIDENCE_PROJECTION_UPGRADE:
            self.assertIn(statement, statements)
        for statement in WORK_UNIT_PERSISTENCE_UPGRADE:
            self.assertNotIn(statement, statements)
        for statement in DELEGATION_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in AGENT_BINDING_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION)

    async def test_a2a_head_advances_artifact_and_evidence(self) -> None:
        connection = FakeConnection(ARTIFACT_PERSISTENCE_DOWN_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in ARTIFACT_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in EVIDENCE_PROJECTION_UPGRADE:
            self.assertIn(statement, statements)
        for statement in A2A_SOURCE_MAPPING_UPGRADE:
            self.assertNotIn(statement, statements)
        for statement in DELEGATION_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in AGENT_BINDING_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION)

    async def test_artifact_head_advances_evidence_and_repairs_table(self) -> None:
        connection = FakeConnection(EVIDENCE_PROJECTION_DOWN_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in EVIDENCE_PROJECTION_UPGRADE:
            self.assertIn(statement, statements)
        artifact_table = next(
            statement
            for statement in ARTIFACT_PERSISTENCE_UPGRADE
            if "CREATE TABLE IF NOT EXISTS mission_artifacts" in statement
        )
        self.assertIn(artifact_table, statements)
        old_event_constraint_change = ARTIFACT_PERSISTENCE_UPGRADE[1]
        self.assertNotIn(old_event_constraint_change, statements)
        self.assertIn(ARTIFACT_TABLE_OWNERSHIP_UPGRADE[0], statements)
        backfill = next(
            statement
            for statement in EVIDENCE_PROJECTION_UPGRADE
            if "INSERT INTO evidence" in statement
        )
        self.assertIn("FROM mission_events", backfill)
        self.assertIn("ON CONFLICT (id) DO NOTHING", backfill)
        for statement in DELEGATION_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in AGENT_BINDING_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION)

    async def test_evidence_head_advances_only_delegation_persistence(self) -> None:
        connection = FakeConnection(EVIDENCE_PROJECTION_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in EVIDENCE_PROJECTION_UPGRADE:
            self.assertNotIn(statement, statements)
        for statement in DELEGATION_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        for statement in AGENT_BINDING_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION)

    async def test_delegation_head_advances_only_agent_binding_persistence(self) -> None:
        connection = FakeConnection(AGENT_BINDING_PERSISTENCE_DOWN_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in DELEGATION_PERSISTENCE_UPGRADE:
            self.assertNotIn(statement, statements)
        for statement in AGENT_BINDING_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION)

    async def test_agent_binding_head_advances_only_catalog_projection(self) -> None:
        connection = FakeConnection(AGENT_CATALOG_PROJECTION_DOWN_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in AGENT_BINDING_PERSISTENCE_UPGRADE:
            self.assertNotIn(statement, statements)
        for statement in AGENT_CATALOG_PROJECTION_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION)

    async def test_catalog_head_advances_only_inbound_source_mapping(self) -> None:
        connection = FakeConnection(A2A_INBOUND_SOURCE_MAPPING_DOWN_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in AGENT_CATALOG_PROJECTION_UPGRADE:
            self.assertNotIn(statement, statements)
        for statement in A2A_INBOUND_SOURCE_MAPPING_UPGRADE:
            self.assertIn(statement, statements)
        inbound_index = A2A_INBOUND_SOURCE_MAPPING_UPGRADE[0]
        self.assertIn("source->>'reference'", inbound_index)
        self.assertIn("source->>'externalId'", inbound_index)
        self.assertEqual(connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION)

    async def test_inbound_head_advances_only_decision_persistence(self) -> None:
        connection = FakeConnection(DECISION_PERSISTENCE_DOWN_REVISION)

        await apply_startup_migrations(connection)

        statements = [sql for sql, _args in connection.executed]
        for statement in A2A_INBOUND_SOURCE_MAPPING_UPGRADE:
            self.assertNotIn(statement, statements)
        for statement in DECISION_PERSISTENCE_UPGRADE:
            self.assertIn(statement, statements)
        decision_table = next(
            statement
            for statement in DECISION_PERSISTENCE_UPGRADE
            if "CREATE TABLE IF NOT EXISTS decisions" in statement
        )
        self.assertIn("UNIQUE (work_unit_id, attempt, context_digest)", decision_table)
        for statement in DECISION_EXPIRY_UPGRADE:
            self.assertIn(statement, statements)
        self.assertEqual(connection.current_revision, ARTIFACT_TABLE_OWNERSHIP_REVISION)

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
