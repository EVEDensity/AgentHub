"""Actual session compatibility migrations in isolated PostgreSQL schemas."""
from __future__ import annotations

import asyncio
import io
import os
import unittest
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import asyncpg
import psycopg2
from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from fastapi import HTTPException
from psycopg2 import sql
from sqlalchemy.engine import make_url

from app.api.v1.chat_mission._confirmation import confirm_chat_pending
from app.db.migrations.session_workspace import SESSION_WORKSPACE_REVISION
from app.domain import (
    ActorRef,
    PendingConfirmation,
    Session,
    SessionEvent,
    SessionEventType,
)
from app.repositories import SessionEventRepository, SessionRepository
from app.repositories.pending_confirmation_repository import (
    PendingConfirmationRepository,
)
from app.services.agent_binding_service import AgentBinding
from app.services.mission_service import MissionService

_DSN = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")


@unittest.skipUnless(_DSN, "AGENTHUB_TEST_POSTGRES_DSN is required for real session migration tests")
class SessionWorkspacePostgresTests(unittest.TestCase):
    @asynccontextmanager
    async def _confirmation_environment(self):
        self._alembic(command.upgrade, "head")
        pool = await asyncpg.create_pool(_DSN, min_size=1, max_size=3,
                                        server_settings={"search_path": self.schema})

        @asynccontextmanager
        async def transaction():
            async with pool.acquire() as connection, connection.transaction():
                yield connection

        class Resolver:
            binding = AgentBinding("executor", "function-calling", ())

            async def list_enabled(self, *, scope_id):
                return [self.binding] if scope_id == "alice" else []

            async def resolve(self, *, scope_id, agent_id):
                return self.binding if scope_id == "alice" and agent_id == "executor" else None

        pendings = PendingConfirmationRepository(
            execute=pool.execute, fetch_one=pool.fetchrow, fetch_all=pool.fetch,
            transaction_factory=transaction,
        )
        now = datetime.now(UTC)
        actor = ActorRef(type="human", id="alice")
        try:
            await SessionRepository(execute=pool.execute, fetch_one=pool.fetchrow, fetch_all=pool.fetch).add_session(
                Session(id="confirmed-session", workspace_id="alice", title="Confirmation",
                        created_by=actor, created_at=now, updated_at=now),
            )
            await pendings.add_pending(PendingConfirmation(
                id="approval", session_id="confirmed-session", workspace_id="alice", rule_id="rule",
                action_kind="create_mission", rule_description="Approve one executor",
                target_agent="executor", message="Approved work",
                created_by=actor, created_at=now, expires_at=now + timedelta(minutes=15),
            ))
            yield pendings, pool, Resolver()
        finally:
            await pool.close()

    def test_concurrent_confirmation_commits_only_one_mission(self):
        async def scenario():
            async with self._confirmation_environment() as (pendings, pool, resolver):
                results = await asyncio.gather(*(
                    confirm_chat_pending("approval", user={"id": "alice", "role": "user"},
                                         pending_repo=pendings, resolver=resolver)
                    for _ in range(2)
                ), return_exceptions=True)
                self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
                failure = next(result for result in results if isinstance(result, HTTPException))
                self.assertEqual(failure.status_code, 409)
                self.assertEqual(await pool.fetchval("SELECT COUNT(*) FROM missions"), 1)
                self.assertEqual(await pool.fetchval("SELECT COUNT(*) FROM work_units"), 1)
                self.assertEqual((await pendings.get_pending("approval")).status.value, "CONFIRMED")
        asyncio.run(scenario())

    def test_confirmation_dispatch_failure_rolls_back_and_can_retry(self):
        async def scenario():
            async with self._confirmation_environment() as (pendings, pool, resolver):
                with (
                    mock.patch.object(MissionService, "create_chat_work_unit", side_effect=RuntimeError("dispatch unavailable")),
                    self.assertRaisesRegex(RuntimeError, "dispatch unavailable"),
                ):
                    await confirm_chat_pending("approval", user={"id": "alice", "role": "user"},
                                               pending_repo=pendings, resolver=resolver)
                self.assertEqual((await pendings.get_pending("approval")).status.value, "PENDING")
                for table in ("missions", "mission_contracts", "work_units", "mission_events", "session_events"):
                    self.assertEqual(await pool.fetchval(f"SELECT COUNT(*) FROM {table}"), 0)
                result = await confirm_chat_pending("approval", user={"id": "alice", "role": "user"},
                                                    pending_repo=pendings, resolver=resolver)
                self.assertEqual(result["status"], "confirmed")
                self.assertEqual(await pool.fetchval("SELECT COUNT(*) FROM missions"), 1)
        asyncio.run(scenario())

    def setUp(self):
        self.schema = "agenthub_session_" + uuid.uuid4().hex
        self.admin = psycopg2.connect(_DSN)
        self.admin.autocommit = True
        self.addCleanup(self.admin.close)
        with self.admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self._drop_schema)
        self.connection = psycopg2.connect(_DSN, options=f"-csearch_path={self.schema}")
        self.connection.autocommit = True
        self.addCleanup(self.connection.close)
        root = Path(__file__).parents[2]
        self.config = Config(str(root / "alembic.ini"), stdout=io.StringIO())
        self.config.set_main_option("script_location", str(root / "migrations"))
        url = make_url(_DSN).update_query_dict({"options": f"-csearch_path={self.schema}"})
        self.config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))

    def _drop_schema(self):
        with self.admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def _execute(self, statement):
        with self.connection.cursor() as cursor:
            cursor.execute(statement)
            return cursor.fetchall() if cursor.description else None

    def _alembic(self, operation, revision):
        with mock.patch.dict(os.environ, {"DATABASE_URL": ""}):
            operation(self.config, revision)

    def test_legacy_rows_stay_unscoped_and_new_sessions_roundtrip_with_events(self):
        self._alembic(command.upgrade, "b7e1f203c4d5")
        self._execute("""INSERT INTO sessions(id,name,owner_id,created_at)
            VALUES('legacy','preserve this conversation','alice','2026-09-01')""")
        self._alembic(command.upgrade, "head")
        self.assertEqual(self._execute("SELECT version_num FROM alembic_version"), [(SESSION_WORKSPACE_REVISION,)])
        self.assertEqual(self._execute("SELECT name,owner_id,workspace_id FROM sessions WHERE id='legacy'"),
                         [("preserve this conversation", "alice", None)])

        async def roundtrip():
            connection = await asyncpg.connect(_DSN, server_settings={"search_path": self.schema})
            try:
                sessions = SessionRepository(execute=connection.execute, fetch_one=connection.fetchrow, fetch_all=connection.fetch)
                events = SessionEventRepository(execute=connection.execute, fetch_one=connection.fetchrow, fetch_all=connection.fetch)
                now = datetime.now(UTC)
                session = Session(id="new", workspace_id="alice", title="Scoped session",
                                  created_by=ActorRef(type="human", id="alice", display_name=""), created_at=now, updated_at=now)
                self.assertIsNone(await sessions.get_session("legacy"))
                await sessions.add_session(session)
                self.assertEqual(await sessions.get_session("new"), session)
                for index in range(3):
                    await events.add_session_event(SessionEvent(
                        id=f"event-{index}", session_id="new", event_type=SessionEventType.MESSAGE_CREATED,
                        actor=ActorRef(type="human", id="alice"), payload={"index": index}, created_at=now,
                    ))
                cursor = await events.get_session_event("event-1")
                self.assertEqual([event.id for event in await events.list_session_events("new", after=cursor)], ["event-2"])
                self.assertEqual(await events.count_session_events("new", after=cursor), 1)
            finally:
                await connection.close()

        asyncio.run(roundtrip())
        self._alembic(command.downgrade, "b7e1f203c4d5")
        self.assertEqual(self._execute("SELECT workspace_id FROM sessions WHERE id='new'"), [("alice",)])
        self.assertEqual(self._execute("SELECT COUNT(*) FROM session_events"), [(3,)])
        self._alembic(command.upgrade, "head")
        self.assertEqual(self._execute("SELECT COUNT(*) FROM sessions"), [(2,)])

    def test_experimental_v1_native_timestamps_gain_legacy_compatibility(self):
        self._alembic(command.stamp, "b7e1f203c4d5")
        self._execute("""CREATE TABLE sessions(
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL,
            status TEXT NOT NULL, metadata JSONB, created_by_type TEXT NOT NULL,
            created_by_id TEXT NOT NULL, created_by_display_name TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
        )""")
        self._execute("""INSERT INTO sessions(id,workspace_id,title,status,created_by_type,
            created_by_id,created_at,updated_at) VALUES('v1','alice','Existing scope','ACTIVE',
            'human','alice','2026-09-01T00:00:00Z','2026-09-01T00:00:00Z')""")
        self._alembic(command.upgrade, "head")
        self.assertEqual(self._execute("SELECT workspace_id,title FROM sessions WHERE id='v1'"), [("alice", "Existing scope")])
        types = self._execute("""SELECT data_type FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name='sessions'
            AND column_name IN ('created_at','updated_at') ORDER BY column_name""")
        self.assertEqual(types, [("text",), ("text",)])

        async def roundtrip():
            connection = await asyncpg.connect(_DSN, server_settings={"search_path": self.schema})
            try:
                sessions = SessionRepository(execute=connection.execute, fetch_one=connection.fetchrow, fetch_all=connection.fetch)
                existing = await sessions.get_session("v1")
                self.assertEqual(existing.workspace_id, "alice")
                self.assertEqual(existing.created_at, datetime(2026, 9, 1, tzinfo=UTC))
                now = datetime.now(UTC)
                new = Session(id="new-v1", workspace_id="alice", title="New scope",
                              created_by=ActorRef(type="human", id="alice", display_name=""), created_at=now, updated_at=now)
                await sessions.add_session(new)
                self.assertEqual(await sessions.get_session(new.id), new)
            finally:
                await connection.close()
        asyncio.run(roundtrip())

    def test_failed_upgrade_rolls_back_new_columns_and_revision(self):
        self._alembic(command.upgrade, "b7e1f203c4d5")
        self._execute("""INSERT INTO sessions(id,name,owner_id,created_at)
            VALUES('legacy','preserve this conversation','alice','2026-09-01')""")
        before = self._execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name='sessions' ORDER BY column_name""")
        execute = Operations.execute

        def fail_mid_migration(operation, statement, *args, **kwargs):
            if "ADD COLUMN IF NOT EXISTS title" in str(statement):
                raise RuntimeError("injected session migration failure")
            return execute(operation, statement, *args, **kwargs)

        with (
            mock.patch.object(Operations, "execute", fail_mid_migration),
            self.assertRaisesRegex(RuntimeError, "injected session migration failure"),
        ):
            self._alembic(command.upgrade, "head")
        self.assertEqual(self._execute("SELECT version_num FROM alembic_version"), [("b7e1f203c4d5",)])
        self.assertEqual(self._execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name='sessions' ORDER BY column_name"""), before)
        self.assertEqual(self._execute("SELECT name,owner_id FROM sessions WHERE id='legacy'"),
                         [("preserve this conversation", "alice")])
        self._alembic(command.upgrade, "head")
        self.assertEqual(self._execute("SELECT workspace_id FROM sessions WHERE id='legacy'"), [(None,)])
