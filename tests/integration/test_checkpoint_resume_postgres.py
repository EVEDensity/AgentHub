"""Actual Alembic install/upgrade/rollback in disposable PostgreSQL schemas."""

from __future__ import annotations

import asyncio
import io
import os
import unittest
import uuid
from pathlib import Path
from unittest import mock

import asyncpg
import psycopg2
from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from psycopg2 import sql
from sqlalchemy.engine import make_url

from app.db.migrations.runner import apply_startup_migrations
from app.repositories import MissionRepository
from tests.domain.factories import build_execution_checkpoint

_DSN = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")
_COLUMNS = {
    "resume_protocol_version",
    "next_action",
    "idempotency_key",
    "workspace_revision",
    "context_manifest_digest",
}


@unittest.skipUnless(
    _DSN, "AGENTHUB_TEST_POSTGRES_DSN is required for checkpoint migration tests"
)
class CheckpointResumePostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = "agenthub_checkpoint_" + uuid.uuid4().hex
        self.admin = psycopg2.connect(_DSN)
        self.admin.autocommit = True
        self.addCleanup(self.admin.close)
        with self.admin.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema))
            )
        self.addCleanup(self._drop_schema)
        self.connection = psycopg2.connect(_DSN, options=f"-csearch_path={self.schema}")
        self.connection.autocommit = True
        self.addCleanup(self.connection.close)
        root = Path(__file__).parents[2]
        self.config = Config(str(root / "alembic.ini"), stdout=io.StringIO())
        self.config.set_main_option("script_location", str(root / "migrations"))
        url = make_url(_DSN).update_query_dict(
            {"options": f"-csearch_path={self.schema}"}
        )
        self.config.set_main_option(
            "sqlalchemy.url",
            url.render_as_string(hide_password=False).replace("%", "%%"),
        )

    def _drop_schema(self) -> None:
        with self.admin.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))
            )

    def _execute(self, statement, parameters=None):
        with self.connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return cursor.fetchall() if cursor.description else None

    def _alembic(self, operation, revision) -> None:
        # Never let an ambient application DATABASE_URL override this isolated
        # schema. All commands use the explicitly supplied disposable DSN.
        with mock.patch.dict(os.environ, {"DATABASE_URL": ""}):
            operation(self.config, revision)

    def _columns(self) -> set[str]:
        return {
            row[0]
            for row in self._execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name='execution_checkpoints'",
                (self.schema,),
            )
        }

    def _original_checkpoint(self) -> None:
        # Minimal parent tables isolate the checkpoint transition from domain
        # seeding. The fresh-install test below also runs the entire chain.
        self._execute("CREATE TABLE missions(id TEXT PRIMARY KEY)")
        self._execute(
            "CREATE TABLE work_units(id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id))"
        )
        self._alembic(command.stamp, "f5c9d0e1a2b3")
        self._alembic(command.upgrade, "a6d0e1f2b3c4")
        self.assertFalse(_COLUMNS & self._columns())
        self._execute("INSERT INTO missions VALUES('mis-1')")
        self._execute("INSERT INTO work_units VALUES('wu-1', 'mis-1')")
        self._execute(
            """INSERT INTO execution_checkpoints(
            id, mission_id, work_unit_id, attempt, sequence, phase, iteration,
            tool_calls, prompt_tokens, completion_tokens, model_cost, terminal,
            state_digest, created_by, created_at
        ) VALUES('old', 'mis-1', 'wu-1', 1, 1, 'harness.execution.started',
            0, 0, 12, 4, 0.25, false, %s, %s::jsonb, '2026-09-01T00:00:00Z')""",
            ("sha256:" + "a" * 64, '{"type":"runner","id":"runner-1"}'),
        )

    def test_empty_database_installs_the_complete_alembic_chain(self) -> None:
        self._alembic(command.upgrade, "head")
        self.assertTrue(_COLUMNS <= self._columns())
        self.assertEqual(
            self._execute("SELECT version_num FROM alembic_version"),
            [("b7e1f203c4d5",)],
        )

    def test_existing_rows_survive_upgrade_downgrade_and_reupgrade(self) -> None:
        self._original_checkpoint()
        before = self._execute(
            "SELECT id, prompt_tokens, model_cost, state_digest FROM execution_checkpoints"
        )
        self._alembic(command.upgrade, "head")
        self.assertEqual(
            self._execute(
                "SELECT id, prompt_tokens, model_cost, state_digest FROM execution_checkpoints"
            ),
            before,
        )
        self.assertEqual(
            self._execute(
                "SELECT resume_protocol_version, next_action, workspace_revision FROM execution_checkpoints"
            ),
            [(None, None, None)],
        )
        self._execute(
            "UPDATE execution_checkpoints SET resume_protocol_version=1, workspace_revision='new-revision'"
        )
        self._alembic(command.downgrade, "a6d0e1f2b3c4")
        self.assertFalse(_COLUMNS & self._columns())
        self.assertEqual(
            self._execute(
                "SELECT id, prompt_tokens, model_cost, state_digest FROM execution_checkpoints"
            ),
            before,
        )
        self._alembic(command.upgrade, "head")
        self._alembic(command.upgrade, "head")
        self.assertEqual(
            self._execute(
                "SELECT COUNT(*), MAX(workspace_revision) FROM execution_checkpoints"
            ),
            [(1, None)],
        )

    def test_preexisting_resume_columns_and_values_are_not_overwritten(self) -> None:
        self._original_checkpoint()
        self._execute(
            "ALTER TABLE execution_checkpoints ADD COLUMN workspace_revision TEXT"
        )
        self._execute(
            "UPDATE execution_checkpoints SET workspace_revision='preserved-revision'"
        )
        self._alembic(command.upgrade, "head")
        self.assertTrue(_COLUMNS <= self._columns())
        self.assertEqual(
            self._execute("SELECT workspace_revision FROM execution_checkpoints"),
            [("preserved-revision",)],
        )

    def test_failed_upgrade_rolls_back_ddl_and_does_not_advance_revision(self) -> None:
        self._original_checkpoint()
        execute = Operations.execute

        def fail_mid_migration(operation, statement, *args, **kwargs):
            if "ADD COLUMN IF NOT EXISTS next_action" in str(statement):
                raise RuntimeError("injected checkpoint migration failure")
            return execute(operation, statement, *args, **kwargs)

        with (
            mock.patch.object(Operations, "execute", fail_mid_migration),
            self.assertRaisesRegex(
                RuntimeError, "injected checkpoint migration failure"
            ),
        ):
            self._alembic(command.upgrade, "head")
        self.assertFalse(_COLUMNS & self._columns())
        self.assertEqual(
            self._execute("SELECT version_num FROM alembic_version"),
            [("a6d0e1f2b3c4",)],
        )
        self.assertEqual(
            self._execute("SELECT COUNT(*) FROM execution_checkpoints"), [(1,)]
        )
        self._alembic(command.upgrade, "head")
        self.assertTrue(_COLUMNS <= self._columns())

    def test_runtime_startup_upgrades_original_head_without_rebuilding_tables(
        self,
    ) -> None:
        self._original_checkpoint()

        async def scenario():
            connection = await asyncpg.connect(
                _DSN, server_settings={"search_path": self.schema}
            )
            try:
                async with connection.transaction():
                    await apply_startup_migrations(connection)
                self.assertEqual(
                    await connection.fetchval(
                        "SELECT version_num FROM alembic_version"
                    ),
                    "b7e1f203c4d5",
                )
                self.assertEqual(
                    await connection.fetchval(
                        "SELECT COUNT(*) FROM execution_checkpoints"
                    ),
                    1,
                )
                # At current head the startup path must be idempotent.
                await apply_startup_migrations(connection)
            finally:
                await connection.close()

        asyncio.run(scenario())
        self.assertTrue(_COLUMNS <= self._columns())

    def test_repository_roundtrips_resume_fields_after_upgrade(self) -> None:
        self._original_checkpoint()
        self._alembic(command.upgrade, "head")
        checkpoint = build_execution_checkpoint(
            id="new",
            sequence=2,
            phase="harness.tool.started",
            resume_protocol_version=1,
            next_action={"toolName": "file_write", "callId": "call-1"},
            idempotency_key="mis-1/wu-1/1/file_write/digest",
            workspace_revision="revision-1",
            context_manifest_digest="manifest-1",
        )

        async def scenario():
            connection = await asyncpg.connect(
                _DSN, server_settings={"search_path": self.schema}
            )
            try:
                repository = MissionRepository(
                    execute=connection.execute,
                    fetch_one=connection.fetchrow,
                    fetch_all=connection.fetch,
                )
                await repository.add_execution_checkpoint(checkpoint)
                restored = await repository.get_execution_checkpoint(checkpoint.id)
                self.assertEqual(restored.to_public_dict(), checkpoint.to_public_dict())
                legacy = await repository.get_execution_checkpoint("old")
                self.assertIsNone(legacy.workspace_revision)
            finally:
                await connection.close()

        asyncio.run(scenario())
