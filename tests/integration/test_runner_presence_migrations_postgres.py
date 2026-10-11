"""Actual narrow c8-to-D9 upgrades preserve durable sessions and rollback atomically."""
from __future__ import annotations

import io
import os
import unittest
import uuid
from pathlib import Path
from unittest import mock

import psycopg2
from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from psycopg2 import sql
from sqlalchemy.engine import make_url

from app.db.migrations.runner_presence import RUNNER_PRESENCE_REVISION
from app.db.migrations.session_workspace import SESSION_WORKSPACE_REVISION

_DSN = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")


@unittest.skipUnless(_DSN, "AGENTHUB_TEST_POSTGRES_DSN is required for actual Runner presence migrations")
class RunnerPresencePostgresMigrationTests(unittest.TestCase):
    def setUp(self):
        self.schema = "runner_presence_" + uuid.uuid4().hex
        self.admin = psycopg2.connect(_DSN)
        self.admin.autocommit = True
        self.addCleanup(self.admin.close)
        with self.admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        self.connection = psycopg2.connect(_DSN, options=f"-csearch_path={self.schema}")
        self.connection.autocommit = True
        self.addCleanup(self.connection.close)
        root = Path(__file__).parents[2]
        self.config = Config(str(root / "alembic.ini"), stdout=io.StringIO())
        self.config.set_main_option("script_location", str(root / "migrations"))
        url = make_url(_DSN).update_query_dict({"options": f"-csearch_path={self.schema}"})
        self.config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
        self.alembic(command.upgrade, SESSION_WORKSPACE_REVISION)
        self.execute("""INSERT INTO sessions(id,name,participants,created_at,owner_id)
            VALUES('preserved-session','Keep existing conversation','[]','2026-09-01','alice')""")

    def drop_schema(self):
        with self.admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def execute(self, statement):
        with self.connection.cursor() as cursor:
            cursor.execute(statement)
            return cursor.fetchall() if cursor.description else None

    def alembic(self, operation, revision):
        with mock.patch.dict(os.environ, {"DATABASE_URL": ""}):
            operation(self.config, revision)

    def test_narrow_upgrade_and_downgrade_preserve_existing_session(self):
        before = self.execute("SELECT * FROM sessions")
        self.alembic(command.upgrade, RUNNER_PRESENCE_REVISION)
        self.assertEqual(self.execute("SELECT version_num FROM alembic_version"), [(RUNNER_PRESENCE_REVISION,)])
        self.assertEqual(self.execute("SELECT COUNT(*) FROM runner_presence"), [(0,)])
        self.assertEqual(self.execute("SELECT * FROM sessions"), before)
        self.alembic(command.downgrade, SESSION_WORKSPACE_REVISION)
        self.assertEqual(self.execute("SELECT version_num FROM alembic_version"), [(SESSION_WORKSPACE_REVISION,)])
        self.assertEqual(self.execute("SELECT * FROM sessions"), before)
        self.assertEqual(self.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='runner_presence'"), [(0,)])
        self.alembic(command.upgrade, RUNNER_PRESENCE_REVISION)
        self.assertEqual(self.execute("SELECT * FROM sessions"), before)

    def test_failure_rolls_back_presence_table_and_revision_marker(self):
        execute = Operations.execute

        def fail_after_table(operation, statement, *args, **kwargs):
            if "CREATE INDEX IF NOT EXISTS idx_runner_presence_binding" in str(statement):
                raise RuntimeError("injected presence migration failure")
            return execute(operation, statement, *args, **kwargs)

        with mock.patch.object(Operations, "execute", fail_after_table), self.assertRaisesRegex(RuntimeError, "injected presence migration failure"):
            self.alembic(command.upgrade, RUNNER_PRESENCE_REVISION)
        self.assertEqual(self.execute("SELECT version_num FROM alembic_version"), [(SESSION_WORKSPACE_REVISION,)])
        self.assertEqual(self.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='runner_presence'"), [(0,)])
        self.assertEqual(self.execute("SELECT name,owner_id FROM sessions WHERE id='preserved-session'"), [("Keep existing conversation", "alice")])
        self.alembic(command.upgrade, RUNNER_PRESENCE_REVISION)
        self.assertEqual(self.execute("SELECT COUNT(*) FROM runner_presence"), [(0,)])
