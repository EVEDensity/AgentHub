"""Durable budget accounting on the real single-writer SQLite pool."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.migrations.mission_budget import upgrade_mission_budget_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain.mission_budget import BudgetConflict, BudgetError, BudgetExhausted
from app.repositories import MissionRepository
from app.repositories.mission_budget_repository import MissionBudgetRepository
from app.services.mission_budget_service import MissionBudgetService
from tests.services.mission_budget_fixtures import (
    add_running_unit, execution_scope, seed_mission, server_prices, trusted_usage,
)

DIGEST = 'sha256:' + 'a' * 64


class MissionBudgetSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.pool = SQLitePool(Path(directory.name) / 'budget.sqlite3')
        await self.pool.initialize()
        self.addAsyncCleanup(self.pool.close)
        async with self.pool.acquire() as connection:
            self.connection = connection
            await _create_mission_control_plane_sqlite(connection)
            await upgrade_mission_budget_sqlite(connection)

        @asynccontextmanager
        async def transaction():
            async with self.pool.acquire() as current:
                async with current.transaction():
                    yield current

        self.repository = MissionRepository(execute=connection.execute, fetch_one=connection.fetchrow,
                                            fetch_all=connection.fetch, transaction_factory=transaction)
        self.ledger = MissionBudgetRepository.from_mission_transaction(self.repository)
        self.service = MissionBudgetService(self.repository, server_prices())
        await seed_mission(self.repository)
        self.scope = execution_scope()

    async def initialize(self, limit='1'):
        return await self.service.initialize_account('mis-1', limit_usd=limit)

    async def reserve(self, operation='model-call-1', *, scope=None, tokens=500_000):
        return await self.service.reserve(scope or self.scope, operation, provider='provider', model='model',
                                          max_input_tokens=tokens, max_output_tokens=0)

    async def dispatch(self, reservation):
        return await self.service.dispatch_once(self.scope, reservation.id, request_digest=DIGEST)

    async def test_reserve_dispatch_settle_are_persistent_and_receipt_is_immutable(self):
        await self.initialize()
        reservation = await self.reserve()
        self.assertEqual((await self.ledger.account('mis-1')).held_nano_usd, 500_000_000)
        self.assertTrue((await self.dispatch(reservation)).claimed)
        settled = await self.service.settle_trusted(reservation.id, trusted_usage())
        self.assertEqual((settled.state.value, settled.actual_nano_usd), ('SETTLED', 100_000_000))
        account = await self.ledger.account('mis-1')
        self.assertEqual((account.held_nano_usd, account.spent_nano_usd), (0, 100_000_000))
        restarted = MissionBudgetService(self.repository, server_prices())
        self.assertEqual(await restarted.settle_trusted(reservation.id, trusted_usage()), settled)
        for statement in ('UPDATE mission_budget_receipts SET actual_nano_usd=0', 'DELETE FROM mission_budget_receipts'):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                await self.connection.execute(statement)
        self.assertEqual((await self.ledger.receipt(reservation.id)).actual_nano_usd, 100_000_000)

    async def test_idempotency_conflicts_and_dispatch_compare_and_set_once(self):
        await self.initialize()
        first = await self.reserve()
        self.assertEqual(await self.reserve(), first)
        with self.assertRaises(BudgetConflict):
            await self.reserve(tokens=400_000)
        claims = await asyncio.gather(self.dispatch(first), self.dispatch(first))
        self.assertEqual(sum(claim.claimed for claim in claims), 1)
        with self.assertRaises(BudgetConflict):
            await self.service.dispatch_once(self.scope, first.id, request_digest='sha256:' + 'b' * 64)
        await self.service.settle_trusted(first.id, trusted_usage())
        with self.assertRaises(BudgetConflict):
            await self.service.settle_trusted(first.id, trusted_usage(input_tokens=200_000))

    async def test_shared_pool_competes_for_last_funds_across_child_work_units(self):
        await self.initialize()
        await add_running_unit(self.repository, 'wu-child', parent='wu-1')
        result = await asyncio.gather(self.reserve(tokens=700_000),
                                      self.reserve(scope=execution_scope('wu-child'), tokens=700_000),
                                      return_exceptions=True)
        self.assertEqual(sum(isinstance(value, BudgetExhausted) for value in result), 1)
        self.assertEqual((await self.ledger.account('mis-1')).held_nano_usd, 700_000_000)

    async def test_operation_names_are_independent_per_work_unit_but_share_one_account(self):
        await self.initialize()
        await add_running_unit(self.repository, 'wu-child', parent='wu-1')
        first = await self.reserve(tokens=400_000)
        second = await self.reserve(scope=execution_scope('wu-child'), tokens=400_000)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual((await self.ledger.account('mis-1')).held_nano_usd, 800_000_000)

    async def test_stale_lease_runner_attempt_and_wrong_reservation_scope_refuse_writes(self):
        await self.initialize()
        reservation = await self.reserve()
        for changes in ({'attempt': 2}, {'lease_id': 'other'}, {'runner_id': 'other'}):
            scope = execution_scope(**changes)
            with self.subTest(changes=changes), self.assertRaises(BudgetError):
                await self.reserve('another', scope=scope)
            with self.assertRaises(BudgetError):
                await self.service.dispatch_once(scope, reservation.id, request_digest=DIGEST)
        await self.connection.execute("UPDATE work_units SET lease=json_set(lease,'$.expiresAt','2000-01-01T00:00:00Z')")
        with self.assertRaises(BudgetError):
            await self.dispatch(reservation)
        self.assertEqual((await self.ledger.reservation(reservation.id)).state.value, 'RESERVED')

    async def test_uncertain_dispatch_retains_hold_and_cannot_be_released(self):
        await self.initialize()
        reservation = await self.reserve()
        await self.dispatch(reservation)
        unknown = await self.service.mark_unknown(reservation.id, reason='timeout')
        self.assertEqual(await self.service.mark_unknown(reservation.id, reason='timeout'), unknown)
        self.assertIsNone(await self.ledger.receipt(reservation.id))
        self.assertEqual((await self.ledger.account('mis-1')).held_nano_usd, 500_000_000)
        with self.assertRaises(BudgetConflict):
            await self.service.release_unsubmitted(reservation.id)

    async def test_missing_usage_is_unknown_but_trusted_zero_is_final_zero(self):
        await self.initialize()
        reservation = await self.reserve()
        await self.dispatch(reservation)
        self.assertEqual((await self.service.settle_trusted(reservation.id, None)).state.value, 'UNKNOWN')
        self.assertIsNone(await self.ledger.receipt(reservation.id))
        settled = await self.service.settle_trusted(reservation.id, trusted_usage(input_tokens=0, output_tokens=0))
        self.assertEqual((settled.state.value, settled.actual_nano_usd), ('SETTLED', 0))
        self.assertEqual((await self.ledger.account('mis-1')).held_nano_usd, 0)

    async def test_late_settlement_never_changes_terminal_work_unit_or_mission(self):
        await self.initialize()
        reservation = await self.reserve()
        await self.dispatch(reservation)
        await self.service.mark_unknown(reservation.id, reason='cancelled')
        await self.connection.execute("UPDATE work_units SET status='CANCELLED',lease=NULL")
        await self.connection.execute("UPDATE missions SET status='CANCELLED'")
        restarted = MissionBudgetService(self.repository, server_prices(version='new-version', rate='999'))
        settled = await restarted.settle_trusted(reservation.id, trusted_usage())
        self.assertEqual(settled.actual_nano_usd, 100_000_000)
        self.assertEqual((await self.repository.get_work_unit('wu-1')).status.value, 'CANCELLED')
        self.assertEqual((await self.repository.get_mission('mis-1')).status.value, 'CANCELLED')

    async def test_unsubmitted_release_is_final_idempotent_and_restores_funds(self):
        await self.initialize()
        reservation = await self.reserve()
        released = await self.service.release_unsubmitted(reservation.id)
        self.assertEqual(await self.service.release_unsubmitted(reservation.id), released)
        self.assertEqual((await self.ledger.account('mis-1')).held_nano_usd, 0)
        with self.assertRaises(BudgetConflict):
            await self.dispatch(reservation)

    async def test_actual_above_reservation_is_fully_charged_and_blocks_future_calls(self):
        await self.initialize()
        reservation = await self.reserve(tokens=100_000)
        unsubmitted = await self.reserve('already-reserved', tokens=100_000)
        await self.dispatch(reservation)
        await self.service.settle_trusted(reservation.id, trusted_usage(input_tokens=200_000))
        account = await self.ledger.account('mis-1')
        self.assertEqual((account.spent_nano_usd, account.held_nano_usd, account.blocked), (200_000_000, 100_000_000, 1))
        with self.assertRaises(BudgetExhausted):
            await self.reserve('new')
        with self.assertRaises(BudgetExhausted):
            await self.dispatch(unsubmitted)
        await self.service.release_unsubmitted(unsubmitted.id)

    async def test_price_version_table_and_trusted_usage_identity_cannot_be_injected(self):
        await self.initialize()
        reservation = await self.reserve()
        for prices in (server_prices(version='v2'), server_prices(rate='999')):
            service = MissionBudgetService(self.repository, prices)
            with self.subTest(version=prices.version), self.assertRaises(BudgetConflict):
                await service.initialize_account('mis-1', limit_usd='1')
            with self.assertRaises(BudgetConflict):
                await service.reserve(self.scope, 'injected', provider='provider', model='model', max_input_tokens=1, max_output_tokens=0)
        await self.dispatch(reservation)
        with self.assertRaises(BudgetConflict):
            await self.service.settle_trusted(reservation.id, trusted_usage(provider='other'))
        with self.assertRaises(BudgetError):
            await self.service.settle_trusted(reservation.id, {'cost': 0})

    async def test_contract_raw_decimal_cap_and_optional_stricter_limit_are_authoritative(self):
        with self.assertRaises(BudgetError):
            await self.initialize(limit='10.000000001')
        account = await self.service.initialize_account('mis-1')
        self.assertEqual(account.contract_limit_nano_usd, 10_000_000_000)
        with self.assertRaises(BudgetConflict):
            await self.initialize(limit='1')
        contract = await self.repository.get_contract('contract-1', 1)
        await self.repository.add_contract_lineage('replacement-contract', 'workspace-1')
        await self.repository.add_contract(contract.model_copy(update={'id': 'replacement-contract', 'version': 2}))
        await self.connection.execute("UPDATE missions SET contract_id='replacement-contract',contract_version=2")
        with self.assertRaises(BudgetConflict):
            await self.reserve()

    async def test_nonrepresentable_raw_contract_amount_cannot_create_an_account(self):
        document = (await self.connection.fetchrow('SELECT document FROM mission_contracts'))['document']
        values = json.loads(document)
        values['budgets']['modelCost'] = 0.0000000001
        await self.connection.execute('UPDATE mission_contracts SET document=$1', json.dumps(values))
        with self.assertRaises(BudgetError):
            await self.service.initialize_account('mis-1')
        self.assertIsNone(await self.ledger.account('mis-1'))

    async def test_failed_reservation_insert_rolls_back_the_hold_in_same_transaction(self):
        await self.initialize()
        insert = MissionBudgetRepository.insert

        async def fail_after_balance(ledger, value):
            if type(value).__name__ == 'BudgetReservation':
                raise RuntimeError('injected insert failure')
            await insert(ledger, value)

        with patch.object(MissionBudgetRepository, 'insert', fail_after_balance), self.assertRaisesRegex(RuntimeError, 'injected'):
            await self.reserve()
        self.assertEqual((await self.ledger.account('mis-1')).held_nano_usd, 0)
        self.assertEqual(await self.connection.fetchval('SELECT COUNT(*) FROM mission_budget_reservations'), 0)

    async def test_server_utc_times_are_ordered_and_idempotent_retries_keep_original_times(self):
        await self.initialize()
        times = ('2026-10-18T01:00:00.000001Z', '2026-10-18T01:00:01.000002Z', '2026-10-18T01:00:02.000003Z')
        with patch('app.services.mission_budget_service._utc_timestamp', side_effect=times) as clock:
            reservation = await self.reserve()
            self.assertEqual(await self.reserve(), reservation)
            claim = await self.dispatch(reservation)
            self.assertEqual((await self.dispatch(reservation)).reservation.dispatched_at, times[1])
            settled = await self.service.settle_trusted(reservation.id, trusted_usage())
            self.assertEqual(await self.service.settle_trusted(reservation.id, trusted_usage()), settled)
            self.assertEqual(clock.call_count, 3)
        self.assertEqual((reservation.created_at, claim.reservation.dispatched_at, settled.finalized_at), times)
        receipt = await self.ledger.receipt(reservation.id)
        self.assertEqual(receipt.finalized_at, settled.finalized_at)
        document = json.loads(receipt.document)
        self.assertEqual((document['createdAt'], document['dispatchedAt'], document['finalizedAt']), times)

    async def test_settlement_over_the_entire_cap_records_full_cost_and_denies_more_calls(self):
        await self.initialize()
        reservation = await self.reserve()
        await self.dispatch(reservation)
        settled = await self.service.settle_trusted(reservation.id, trusted_usage(input_tokens=2_000_000))
        self.assertEqual(settled.actual_nano_usd, 2_000_000_000)
        account = await self.ledger.account('mis-1')
        self.assertGreater(account.spent_nano_usd, account.limit_nano_usd)
        with self.assertRaises(BudgetExhausted):
            await self.reserve('after-overspend', tokens=0)

    async def test_one_provider_receipt_cannot_be_charged_to_two_operations(self):
        await self.initialize()
        first = await self.reserve('first')
        second = await self.reserve('second')
        await self.dispatch(first)
        await self.dispatch(second)
        await self.service.settle_trusted(first.id, trusted_usage())
        with self.assertRaises(BudgetConflict):
            await self.service.settle_trusted(second.id, trusted_usage())
        account = await self.ledger.account('mis-1')
        self.assertEqual((account.spent_nano_usd, account.held_nano_usd), (100_000_000, 500_000_000))
        self.assertEqual((await self.ledger.reservation(second.id)).state.value, 'DISPATCHED')
