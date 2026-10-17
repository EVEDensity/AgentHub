"""Real SQLite upgrades, enforced ledger constraints and immutable receipts."""
from __future__ import annotations

import sqlite3
import logging
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest import mock

from app.db.init_db import _ainit_sqlite
from app.db.migrations.mission_budget import MISSION_BUDGET_SQLITE_UPGRADE, upgrade_mission_budget_sqlite
from app.db.migrations.runner import _advance_budget_head, apply_startup_migrations
from app.db.migrations.runner_presence import RUNNER_PRESENCE_REVISION
from app.db.mission_budget_capability import UnsupportedBudgetBackendError
from app.db.neon_http import NeonHttpConnection
from app.db.sqlite_pool import SQLitePool

_CREATED = "2026-10-18T00:00:00.000000Z"
_DISPATCHED = "2026-10-18T00:00:00.000001Z"
_FINALIZED = "2026-10-18T00:00:00.000002Z"


class MissionBudgetSQLiteUpgradeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    async def initialize(self, pool):
        with mock.patch("app.db.session.aget_pool", new=mock.AsyncMock(return_value=pool)):
            await _ainit_sqlite()

    async def profile(self, version):
        pool = SQLitePool(self.directory / f"v{version}.sqlite3")
        await pool.initialize()
        self.addAsyncCleanup(pool.close)
        await self.initialize(pool)
        async with pool.acquire() as connection:
            for table in ("mission_budget_receipts", "mission_budget_reservations", "mission_budget_accounts"):
                await connection.execute(f"DROP TABLE {table}")
            await connection.execute("DROP INDEX uq_mission_budget_contract_revision")
            if version < 5:
                await connection.execute("DROP TABLE runner_presence")
            if version < 4:
                await connection.execute("ALTER TABLE sessions DROP COLUMN title")
            if version == 2:
                await connection.execute("ALTER TABLE execution_checkpoints DROP COLUMN next_action")
            await connection.execute("DELETE FROM schema_migrations")
            await connection.execute("INSERT INTO schema_migrations VALUES($1,'original-profile')", version)
            await connection.execute("UPDATE users SET name='preserved',password_hash='preserved-hash'")
        return pool

    async def test_versions_two_through_five_upgrade_without_reseeding(self):
        for version in (2, 3, 4, 5):
            with self.subTest(version=version):
                pool = await self.profile(version)
                with mock.patch("app.db.sqlite_initializer._bootstrap_legacy", side_effect=AssertionError("no reseeding")):
                    await self.initialize(pool)
                    await self.initialize(pool)
                async with pool.acquire() as connection:
                    self.assertEqual(await connection.fetchval("SELECT MAX(version) FROM schema_migrations"), 6)
                    self.assertEqual(await connection.fetchval("SELECT COUNT(*) FROM schema_migrations WHERE version=6"), 1)
                    for table in ("mission_budget_accounts", "mission_budget_reservations", "mission_budget_receipts"):
                        self.assertEqual(await connection.fetchval(f"SELECT COUNT(*) FROM {table}"), 0)
                    self.assertEqual(await connection.fetchval("SELECT COUNT(*) FROM users WHERE password_hash='preserved-hash'"), 1)
                    self.assertIn("next_action", {row["name"] for row in await connection.fetch("PRAGMA table_info(execution_checkpoints)")})
                    self.assertIn("title", {row["name"] for row in await connection.fetch("PRAGMA table_info(sessions)")})

    async def test_failure_rolls_back_every_budget_table_and_marker(self):
        pool = await self.profile(5)

        class FailingConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            async def execute(self, statement, *args):
                if "CREATE TRIGGER IF NOT EXISTS mission_budget_receipts_no_delete" in statement:
                    raise RuntimeError("injected budget migration failure")
                return await self.connection.execute(statement, *args)

        class FailingPool:
            @asynccontextmanager
            async def acquire(inner_self):
                async with pool.acquire() as connection:
                    yield FailingConnection(connection)

        with self.assertRaisesRegex(RuntimeError, "injected budget migration failure"):
            await self.initialize(FailingPool())
        async with pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("SELECT MAX(version) FROM schema_migrations"), 5)
            self.assertEqual(await connection.fetchval("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'mission_budget_%'"), 0)
            self.assertEqual(await connection.fetchval("SELECT COUNT(*) FROM sqlite_master WHERE name='uq_mission_budget_contract_revision'"), 0)
        await self.initialize(pool)
        async with pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("SELECT MAX(version) FROM schema_migrations"), 6)


class MissionBudgetSQLiteConstraintTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.execute("PRAGMA foreign_keys=ON")
        for statement in (
            "CREATE TABLE missions(id TEXT PRIMARY KEY)",
            "CREATE TABLE mission_contracts(id TEXT PRIMARY KEY,version INTEGER)",
            "CREATE TABLE work_units(id TEXT PRIMARY KEY)",
            "INSERT INTO missions VALUES('mission')",
            "INSERT INTO mission_contracts VALUES('contract',1)",
            "INSERT INTO work_units VALUES('work')",
            *MISSION_BUDGET_SQLITE_UPGRADE,
        ):
            self.connection.execute(statement)
        self.insert("mission_budget_accounts", dict(mission_id="mission", contract_id="contract", contract_version=1,
                    contract_limit_nano_usd=100, limit_nano_usd=80, price_version="p1", pricing_digest="sha256:pricing"))

    def insert(self, table, values):
        columns = ",".join(values)
        placeholders = ",".join("?" for _ in values)
        self.connection.execute(f"INSERT INTO {table}({columns}) VALUES({placeholders})", tuple(values.values()))

    def reservation(self, identifier="reservation", **changes):
        values = dict(id=identifier, mission_id="mission", work_unit_id="work", attempt=1,
                      operation_id=identifier, lease_id="lease", runner_id="runner", payload_digest="digest",
                      request_json="{}", reserved_nano_usd=20, state="RESERVED", created_at=_CREATED)
        values.update(changes)
        if values["state"] in {"DISPATCHED", "UNKNOWN", "SETTLED"}:
            values.setdefault("dispatched_at", _DISPATCHED)
        if values["state"] in {"SETTLED", "RELEASED"}:
            values.setdefault("finalized_at", _FINALIZED)
        self.insert("mission_budget_reservations", values)

    def receipt(self, identifier="reservation", **changes):
        values = dict(reservation_id=identifier, mission_id="mission", disposition="SETTLED", payload_digest="digest",
                      actual_nano_usd=25, document="{}", provider="provider", provider_receipt_id=identifier, finalized_at=_FINALIZED)
        values.update(changes)
        self.insert("mission_budget_receipts", values)

    def test_money_is_nonnegative_integral_bigint_and_spending_can_exceed_cap(self):
        for column in ("contract_limit_nano_usd", "limit_nano_usd", "held_nano_usd", "spent_nano_usd"):
            for value in ("-1", "0.5", "9223372036854775808"):
                with self.subTest(column=column, value=value), self.assertRaises(sqlite3.IntegrityError):
                    self.connection.execute(f"UPDATE mission_budget_accounts SET {column}={value}")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("UPDATE mission_budget_accounts SET limit_nano_usd=101")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("UPDATE mission_budget_accounts SET blocked=2")
        self.connection.execute("UPDATE mission_budget_accounts SET spent_nano_usd=101,blocked=1")
        self.assertEqual(self.connection.execute("SELECT spent_nano_usd,blocked FROM mission_budget_accounts").fetchone(), (101, 1))

    def test_state_consistency_and_operation_uniqueness_are_enforced(self):
        valid = (
            {}, {"state": "DISPATCHED", "dispatch_digest": "dispatch"},
            {"state": "UNKNOWN", "dispatch_digest": "dispatch", "unknown_reason": "lost response"},
            {"state": "SETTLED", "dispatch_digest": "dispatch", "actual_nano_usd": 50},
            {"state": "RELEASED", "actual_nano_usd": 0},
        )
        for index, changes in enumerate(valid):
            self.reservation(f"valid-{index}", **changes)
        invalid = (
            {"state": "UNKNOWN", "dispatch_digest": "dispatch"}, {"state": "DISPATCHED"},
            {"dispatch_digest": "unexpected"}, {"actual_nano_usd": 0},
            {"state": "RELEASED"}, {"state": "RELEASED", "actual_nano_usd": 1},
            {"state": "SETTLED", "dispatch_digest": "dispatch"}, {"state": "invented"},
            {"id": None}, {"attempt": 0}, {"operation_id": ""}, {"reserved_nano_usd": 0.5},
            {"state": "SETTLED", "dispatch_digest": "dispatch", "actual_nano_usd": 0.5},
        )
        for index, changes in enumerate(invalid):
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                self.reservation(f"invalid-{index}", **changes)
        with self.assertRaises(sqlite3.IntegrityError):
            self.reservation("another", operation_id="valid-0")

    def test_foreign_keys_and_contract_revision_are_real(self):
        for statement in (
            "UPDATE mission_budget_accounts SET contract_version=2",
            "UPDATE mission_budget_accounts SET mission_id='missing'",
            "UPDATE mission_budget_accounts SET mission_id=NULL",
        ):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(statement)
        with self.assertRaises(sqlite3.IntegrityError):
            self.reservation(work_unit_id="missing")

    def test_server_timestamps_match_state_utc_format_and_causal_order(self):
        invalid = (
            {"created_at": None}, {"created_at": ""}, {"created_at": _CREATED[:-1] + "+00:00"},
            {"created_at": "2026-10-18 00:00:00.000000Z"}, {"dispatched_at": _DISPATCHED},
            {"finalized_at": _FINALIZED},
            {"state": "DISPATCHED", "dispatch_digest": "dispatch", "dispatched_at": None},
            {"state": "DISPATCHED", "dispatch_digest": "dispatch", "finalized_at": _FINALIZED},
            {"state": "DISPATCHED", "dispatch_digest": "dispatch", "dispatched_at": "2026-10-17T00:00:00.000000Z"},
            {"state": "UNKNOWN", "dispatch_digest": "dispatch", "unknown_reason": "unknown", "dispatched_at": None},
            {"state": "SETTLED", "dispatch_digest": "dispatch", "actual_nano_usd": 1, "finalized_at": None},
            {"state": "SETTLED", "dispatch_digest": "dispatch", "actual_nano_usd": 1, "finalized_at": _CREATED},
            {"state": "RELEASED", "actual_nano_usd": 0, "finalized_at": None},
            {"state": "RELEASED", "actual_nano_usd": 0, "finalized_at": "2026-10-17T00:00:00.000000Z"},
        )
        for index, changes in enumerate(invalid):
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                self.reservation(f"invalid-time-{index}", **changes)
        self.reservation("same-time", state="SETTLED", dispatch_digest="dispatch", actual_nano_usd=1,
                         dispatched_at=_CREATED, finalized_at=_CREATED)
        self.assertEqual(self.connection.execute("SELECT created_at,dispatched_at,finalized_at FROM mission_budget_reservations").fetchone(),
                         (_CREATED, _CREATED, _CREATED))

    def test_final_receipts_are_insert_only_and_provider_identity_is_unique(self):
        self.reservation()
        self.receipt()
        for statement in ("UPDATE mission_budget_receipts SET actual_nano_usd=0", "DELETE FROM mission_budget_receipts"):
            with self.subTest(statement=statement), self.assertRaisesRegex(sqlite3.IntegrityError, "insert-only"):
                self.connection.execute(statement)
        self.reservation("second")
        with self.assertRaises(sqlite3.IntegrityError):
            self.receipt("second", provider_receipt_id="reservation")
        self.assertEqual(self.connection.execute("SELECT actual_nano_usd FROM mission_budget_receipts").fetchall(), [(25,)])

    def test_insert_or_replace_cannot_mutate_receipt_with_recursive_triggers_off(self):
        self.connection.execute("PRAGMA recursive_triggers=OFF")
        self.assertEqual(self.connection.execute("PRAGMA recursive_triggers").fetchone(), (0,))
        self.reservation(state="SETTLED", dispatch_digest="dispatch", actual_nano_usd=25)
        self.reservation("second")
        self.receipt()
        self.connection.execute("UPDATE mission_budget_accounts SET spent_nano_usd=25")
        receipts = self.connection.execute("SELECT * FROM mission_budget_receipts").fetchall()
        account = self.connection.execute("SELECT * FROM mission_budget_accounts").fetchall()
        attempts = (
            ("reservation", "replacement-provider-receipt"),
            ("second", "reservation"),
        )
        for reservation_id, provider_receipt_id in attempts:
            with self.subTest(reservation_id=reservation_id), self.assertRaisesRegex(sqlite3.IntegrityError, "insert-only"):
                self.connection.execute("""INSERT OR REPLACE INTO mission_budget_receipts(
                    reservation_id,mission_id,disposition,payload_digest,actual_nano_usd,document,
                    finalized_at,provider,provider_receipt_id) VALUES(?, 'mission','SETTLED','changed',0,'{}',?,'provider',?)""",
                    (reservation_id, _FINALIZED, provider_receipt_id))
            self.assertEqual(self.connection.execute("SELECT * FROM mission_budget_receipts").fetchall(), receipts)
            self.assertEqual(self.connection.execute("SELECT * FROM mission_budget_accounts").fetchall(), account)

    def test_receipt_disposition_cannot_hide_unpriced_or_released_cost(self):
        invalid = (
            {"provider": None}, {"provider_receipt_id": None}, {"actual_nano_usd": 0.5},
            {"reservation_id": None},
            {"finalized_at": None}, {"finalized_at": ""}, {"finalized_at": _FINALIZED[:-1] + "+00:00"},
            {"disposition": "RELEASED", "provider": None, "provider_receipt_id": None, "actual_nano_usd": 1},
            {"disposition": "RELEASED", "actual_nano_usd": 0}, {"disposition": "UNKNOWN"},
        )
        for index, changes in enumerate(invalid):
            identifier = f"receipt-{index}"
            self.reservation(identifier)
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                self.receipt(identifier, **changes)
        self.reservation("released")
        self.receipt("released", disposition="RELEASED", provider=None, provider_receipt_id=None, actual_nano_usd=0)


class MissionBudgetStatelessMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_neon_refused_before_budget_transaction_ddl_or_stamp(self):
        connection = NeonHttpConnection()
        with mock.patch("app.db.neon_http._http_query", new_callable=mock.AsyncMock) as transport:
            with self.assertRaisesRegex(UnsupportedBudgetBackendError, "stateless"):
                await upgrade_mission_budget_sqlite(connection)
            with self.assertRaisesRegex(UnsupportedBudgetBackendError, "stateless"):
                await _advance_budget_head(connection, RUNNER_PRESENCE_REVISION, logging.getLogger(__name__))
            transport.assert_not_awaited()

    async def test_neon_startup_d9_refused_before_any_budget_ddl_or_head_marker(self):
        connection = NeonHttpConnection()

        async def transport(statement, *args, **kwargs):
            if statement.startswith("SELECT version_num"):
                return {"fields": [], "rows": [{"version_num": RUNNER_PRESENCE_REVISION}], "command": "SELECT"}
            return {"fields": [], "rows": [], "command": "CREATE TABLE"}

        with mock.patch("app.db.neon_http._http_query", new_callable=mock.AsyncMock, side_effect=transport) as mocked:
            with self.assertRaisesRegex(UnsupportedBudgetBackendError, "stateless"):
                await apply_startup_migrations(connection)
        statements = [call.args[0] for call in mocked.await_args_list]
        self.assertEqual(len(statements), 2)
        self.assertTrue(statements[0].startswith("CREATE TABLE IF NOT EXISTS alembic_version"))
        self.assertEqual(statements[1], "SELECT version_num FROM alembic_version LIMIT 1")
