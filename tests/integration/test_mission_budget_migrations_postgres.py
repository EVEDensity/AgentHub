"""Actual PostgreSQL additive budget migration, constraints and rollback."""
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

from app.db.migrations.mission_budget import MISSION_BUDGET_REVISION
from app.db.migrations.runner import apply_startup_migrations
from app.db.migrations.runner_presence import RUNNER_PRESENCE_REVISION

_DSN = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")
_TABLES = ("mission_budget_accounts", "mission_budget_reservations", "mission_budget_receipts")


@unittest.skipUnless(_DSN, "AGENTHUB_TEST_POSTGRES_DSN is required for actual budget migrations")
class MissionBudgetPostgresMigrationTests(unittest.TestCase):
    def setUp(self):
        self.schema = "mission_budget_" + uuid.uuid4().hex
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
        self.alembic(command.upgrade, RUNNER_PRESENCE_REVISION)
        self.execute("INSERT INTO mission_contract_lineages(contract_id,workspace_id) VALUES('contract','workspace')")
        self.execute("""INSERT INTO mission_contracts(id,version,document)
            VALUES('contract',1,'{"id":"contract","version":1}')""")
        self.execute("""INSERT INTO missions(id,workspace_id,title,objective,source,contract_id,contract_version,
            status,created_by,created_at,updated_at) VALUES('mission','workspace','Preserve','Existing work','{}',
            'contract',1,'READY','{"type":"human","id":"owner"}',now(),now())""")
        self.execute("""INSERT INTO work_units(id,mission_id,kind,dependencies,input_refs,expected_outputs,
            required_capabilities,status) VALUES('work','mission','agent.task','[]','[]','[]','[]','PENDING')""")

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

    def version(self):
        return self.execute("SELECT version_num FROM alembic_version")[0][0]

    def budget_tables(self):
        return {row[0] for row in self.execute("""SELECT table_name FROM information_schema.tables
            WHERE table_schema=current_schema() AND table_name LIKE 'mission_budget_%'""")}

    def account(self):
        self.execute("""INSERT INTO mission_budget_accounts(mission_id,contract_id,contract_version,
            contract_limit_nano_usd,limit_nano_usd,price_version,pricing_digest)
            VALUES('mission','contract',1,100,80,'p1','sha256:pricing')""")

    def reservation(self, identifier="reservation"):
        self.execute(f"""INSERT INTO mission_budget_reservations(id,mission_id,work_unit_id,attempt,operation_id,
            lease_id,runner_id,payload_digest,request_json,reserved_nano_usd,state,created_at)
            VALUES('{identifier}','mission','work',1,'{identifier}','lease','runner','digest','{{}}',20,'RESERVED','2026-10-18T00:00:00.000000Z')""")

    def test_actual_head_downgrade_reupgrade_preserves_existing_mission(self):
        before = self.execute("SELECT * FROM missions")
        self.alembic(command.upgrade, "head")
        self.assertEqual(self.version(), MISSION_BUDGET_REVISION)
        self.assertEqual(self.budget_tables(), set(_TABLES))
        self.assertEqual(self.execute("SELECT * FROM missions"), before)
        self.account()
        self.alembic(command.downgrade, RUNNER_PRESENCE_REVISION)
        self.assertEqual(self.version(), RUNNER_PRESENCE_REVISION)
        self.assertEqual(self.budget_tables(), set())
        self.assertEqual(self.execute("SELECT * FROM missions"), before)
        self.alembic(command.upgrade, "head")
        self.account()
        self.assertEqual(self.execute("SELECT * FROM missions"), before)

    def test_failure_rolls_back_budget_tables_function_and_revision(self):
        execute = Operations.execute

        def fail_after_tables(operation, statement, *args, **kwargs):
            if "CREATE TRIGGER mission_budget_receipts_immutable" in str(statement):
                raise RuntimeError("injected budget migration failure")
            return execute(operation, statement, *args, **kwargs)

        with mock.patch.object(Operations, "execute", fail_after_tables), self.assertRaisesRegex(RuntimeError, "injected budget migration failure"):
            self.alembic(command.upgrade, "head")
        self.assertEqual(self.version(), RUNNER_PRESENCE_REVISION)
        self.assertEqual(self.budget_tables(), set())
        self.assertEqual(self.execute("SELECT to_regprocedure('reject_mission_budget_receipt_mutation()')"), [(None,)])
        self.alembic(command.upgrade, "head")
        self.assertEqual(self.budget_tables(), set(_TABLES))

    def test_postgres_checks_and_insert_only_receipts_are_enforced(self):
        self.alembic(command.upgrade, "head")
        self.account()
        invalid = (
            "UPDATE mission_budget_accounts SET limit_nano_usd=101",
            "UPDATE mission_budget_accounts SET held_nano_usd=-1",
            "UPDATE mission_budget_accounts SET spent_nano_usd=9223372036854775808",
            "UPDATE mission_budget_accounts SET blocked=2",
            "UPDATE mission_budget_accounts SET contract_version=2",
            "UPDATE mission_budget_accounts SET price_version=''",
        )
        for statement in invalid:
            with self.subTest(statement=statement), self.assertRaises(psycopg2.Error):
                self.execute(statement)
        self.execute("UPDATE mission_budget_accounts SET spent_nano_usd=101,blocked=1")
        self.assertEqual(self.execute("SELECT spent_nano_usd,blocked FROM mission_budget_accounts"), [(101, 1)])
        self.reservation()
        for statement in (
            "UPDATE mission_budget_reservations SET state='DISPATCHED'",
            "UPDATE mission_budget_reservations SET state='UNKNOWN',dispatch_digest='digest'",
            "UPDATE mission_budget_reservations SET state='RELEASED'",
            "UPDATE mission_budget_reservations SET actual_nano_usd=0",
            "UPDATE mission_budget_reservations SET reserved_nano_usd=-1",
        ):
            with self.subTest(statement=statement), self.assertRaises(psycopg2.Error):
                self.execute(statement)
        self.execute("""UPDATE mission_budget_reservations SET state='SETTLED',dispatch_digest='dispatch',actual_nano_usd=25,
            dispatched_at='2026-10-18T00:00:00.000001Z',finalized_at='2026-10-18T00:00:00.000002Z'""")
        self.execute("""INSERT INTO mission_budget_receipts(reservation_id,mission_id,disposition,payload_digest,
            actual_nano_usd,document,provider,provider_receipt_id,finalized_at)
            VALUES('reservation','mission','SETTLED','digest',25,'{}','provider','provider-receipt','2026-10-18T00:00:00.000002Z')""")
        for statement in ("UPDATE mission_budget_receipts SET actual_nano_usd=0", "DELETE FROM mission_budget_receipts"):
            with self.subTest(statement=statement), self.assertRaisesRegex(psycopg2.Error, "insert-only"):
                self.execute(statement)
        self.reservation("second")
        with self.assertRaises(psycopg2.IntegrityError):
            self.execute("""INSERT INTO mission_budget_receipts(reservation_id,mission_id,disposition,payload_digest,
                actual_nano_usd,document,provider,provider_receipt_id,finalized_at)
                VALUES('second','mission','SETTLED','digest',25,'{}','provider','provider-receipt','2026-10-18T00:00:00.000002Z')""")

    def test_runtime_startup_budget_upgrade_is_atomic_and_repeatable(self):
        async def scenario():
            connection = await asyncpg.connect(_DSN, server_settings={"search_path": self.schema})
            try:
                class FailingConnection:
                    def __getattr__(inner_self, name):
                        return getattr(connection, name)

                    async def execute(inner_self, statement, *args):
                        if "CREATE TRIGGER mission_budget_receipts_immutable" in statement:
                            raise RuntimeError("injected startup budget failure")
                        return await connection.execute(statement, *args)

                with self.assertRaisesRegex(RuntimeError, "injected startup budget failure"):
                    await apply_startup_migrations(FailingConnection())
                self.assertEqual(await connection.fetchval("SELECT version_num FROM alembic_version"), RUNNER_PRESENCE_REVISION)
                self.assertEqual(await connection.fetchval("SELECT to_regclass('mission_budget_accounts')"), None)
                await apply_startup_migrations(connection)
                await apply_startup_migrations(connection)
                self.assertEqual(await connection.fetchval("SELECT version_num FROM alembic_version"), MISSION_BUDGET_REVISION)
            finally:
                await connection.close()

        asyncio.run(scenario())

    def test_postgres_server_timestamps_require_utc_causal_state(self):
        self.alembic(command.upgrade, "head")
        self.account()
        self.reservation()
        for statement in (
            "UPDATE mission_budget_reservations SET created_at=NULL",
            "UPDATE mission_budget_reservations SET created_at=''",
            "UPDATE mission_budget_reservations SET created_at='2026-10-18T00:00:00.000000+00:00'",
            "UPDATE mission_budget_reservations SET dispatched_at='2026-10-18T00:00:00.000001Z'",
            "UPDATE mission_budget_reservations SET finalized_at='2026-10-18T00:00:00.000002Z'",
            "UPDATE mission_budget_reservations SET state='DISPATCHED',dispatch_digest='dispatch'",
            """UPDATE mission_budget_reservations SET state='DISPATCHED',dispatch_digest='dispatch',
                dispatched_at='2026-10-17T00:00:00.000001Z'""",
            """UPDATE mission_budget_reservations SET state='SETTLED',dispatch_digest='dispatch',actual_nano_usd=1,
                dispatched_at='2026-10-18T00:00:00.000001Z',finalized_at='2026-10-18T00:00:00.000000Z'""",
            "UPDATE mission_budget_reservations SET state='RELEASED',actual_nano_usd=0",
        ):
            with self.subTest(statement=statement), self.assertRaises(psycopg2.Error):
                self.execute(statement)
        self.execute("""UPDATE mission_budget_reservations SET state='RELEASED',actual_nano_usd=0,
            finalized_at='2026-10-18T00:00:00.000002Z'""")
        for finalized in ("NULL", "''", "'2026-10-18T00:00:00.000002+00:00'"):
            with self.subTest(finalized=finalized), self.assertRaises(psycopg2.Error):
                self.execute(f"""INSERT INTO mission_budget_receipts(reservation_id,mission_id,disposition,
                    payload_digest,actual_nano_usd,document,finalized_at)
                    VALUES('reservation','mission','RELEASED','digest',0,'{{}}',{finalized})""")
        self.execute("""INSERT INTO mission_budget_receipts(reservation_id,mission_id,disposition,
            payload_digest,actual_nano_usd,document,finalized_at)
            VALUES('reservation','mission','RELEASED','digest',0,'{}','2026-10-18T00:00:00.000002Z')""")
