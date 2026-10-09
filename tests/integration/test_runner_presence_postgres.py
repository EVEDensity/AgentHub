"""Actual Alembic presence storage and expiry/capability matching in PostgreSQL."""
from __future__ import annotations

import asyncio
import io
import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import asyncpg
import psycopg2
from alembic import command
from alembic.config import Config
from psycopg2 import sql
from sqlalchemy.engine import make_url

from app.repositories.runner_presence_repository import RunnerPresenceRepository

_DSN = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")


@unittest.skipUnless(_DSN, "AGENTHUB_TEST_POSTGRES_DSN is required for real presence storage tests")
class RunnerPresencePostgresTests(unittest.TestCase):
    def setUp(self):
        self.schema = "agenthub_presence_" + uuid.uuid4().hex
        self.admin = psycopg2.connect(_DSN)
        self.admin.autocommit = True
        self.addCleanup(self.admin.close)
        with self.admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self._drop_schema)
        root = Path(__file__).parents[2]
        self.config = Config(str(root / "alembic.ini"), stdout=io.StringIO())
        self.config.set_main_option("script_location", str(root / "migrations"))
        url = make_url(_DSN).update_query_dict({"options": f"-csearch_path={self.schema}"})
        self.config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
        with mock.patch.dict(os.environ, {"DATABASE_URL": ""}):
            command.upgrade(self.config, "head")

    def _drop_schema(self):
        with self.admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_presence_requires_current_exact_binding_kind_and_capabilities(self):
        async def scenario():
            connection = await asyncpg.connect(_DSN, server_settings={"search_path": self.schema})
            try:
                repository = RunnerPresenceRepository(execute=connection.execute, fetch_one=connection.fetchrow, fetch_all=connection.fetch)
                now = datetime.now(UTC)
                await repository.observe_poll("alice", runner_id="runner-a", agent_id="executor", adapter_type="function-calling", supported_work_unit_kinds=("desktop.task",), supported_capabilities=("repository.read",), observed_at=now)
                arguments = {"workspace_id": "alice", "agent_id": "executor", "adapter_type": "function-calling", "work_unit_kind": "desktop.task", "now": now}
                self.assertIsNotNone(await repository.matching_observation(**arguments, required_capabilities=("repository.read",)))
                for update in ({"workspace_id": "bob"}, {"agent_id": "other"}, {"adapter_type": "other"}, {"work_unit_kind": "other"}, {"required_capabilities": ("repository.write",)}, {"now": now + timedelta(seconds=31)}):
                    self.assertIsNone(await repository.matching_observation(**{**arguments, **update}))
                row = await connection.fetchrow("SELECT supported_capabilities,last_seen_at,expires_at FROM runner_presence")
                self.assertEqual(row["expires_at"] - row["last_seen_at"], timedelta(seconds=30))
                await connection.execute("UPDATE runner_presence SET supported_capabilities='false'::jsonb")
                self.assertIsNone(await repository.matching_observation(**arguments))
            finally:
                await connection.close()
        asyncio.run(scenario())

    def test_rollback_discards_only_ephemeral_observations_and_reupgrade_is_safe(self):
        async def count_tables():
            connection = await asyncpg.connect(_DSN, server_settings={"search_path": self.schema})
            try:
                return await connection.fetchval("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=current_schema() AND table_name IN ('missions','work_units','sessions')")
            finally:
                await connection.close()
        before = asyncio.run(count_tables())
        with mock.patch.dict(os.environ, {"DATABASE_URL": ""}):
            command.downgrade(self.config, "c8f2a314d5e6")
        self.assertEqual(asyncio.run(count_tables()), before)
        with mock.patch.dict(os.environ, {"DATABASE_URL": ""}):
            command.upgrade(self.config, "head")
            command.upgrade(self.config, "head")
        self.assertEqual(asyncio.run(count_tables()), before)
