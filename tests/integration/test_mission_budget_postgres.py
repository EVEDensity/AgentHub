"""Actual PostgreSQL row locks across independent connections; opt-in DSN."""
from __future__ import annotations

import asyncio
import io
import os
import unittest
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import asyncpg
import psycopg2
from alembic import command
from alembic.config import Config
from psycopg2 import sql
from sqlalchemy.engine import make_url

from app.domain.mission_budget import BudgetConflict, BudgetExhausted
from app.repositories import MissionRepository
from app.repositories.mission_budget_repository import MissionBudgetRepository
from app.services.mission_budget_service import MissionBudgetService
from tests.services.mission_budget_fixtures import execution_scope, seed_mission, server_prices, trusted_usage

_DSN = os.getenv('AGENTHUB_TEST_POSTGRES_DSN')
DIGEST = 'sha256:' + 'a' * 64


def connected_repository(connection):
    @asynccontextmanager
    async def transaction():
        async with connection.transaction():
            yield connection
    return MissionRepository(execute=connection.execute, fetch_one=connection.fetchrow,
                             fetch_all=connection.fetch, transaction_factory=transaction)


@unittest.skipUnless(_DSN, 'AGENTHUB_TEST_POSTGRES_DSN is required for real budget row-lock tests')
class MissionBudgetPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = 'mission_budget_' + uuid.uuid4().hex
        self.admin = psycopg2.connect(_DSN)
        self.admin.autocommit = True
        self.addCleanup(self.admin.close)
        with self.admin.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        root = Path(__file__).parents[2]
        config = Config(str(root / 'alembic.ini'), stdout=io.StringIO())
        config.set_main_option('script_location', str(root / 'migrations'))
        url = make_url(_DSN).update_query_dict({'options': f'-csearch_path={self.schema}'})
        config.set_main_option('sqlalchemy.url', url.render_as_string(hide_password=False).replace('%', '%%'))
        with patch.dict(os.environ, {'DATABASE_URL': ''}):
            command.upgrade(config, 'head')
        self.connections = []
        for _index in range(2):
            connection = await asyncpg.connect(_DSN, server_settings={'search_path': self.schema})
            self.connections.append(connection)
            self.addAsyncCleanup(connection.close)
        self.repositories = [connected_repository(connection) for connection in self.connections]
        self.services = [MissionBudgetService(repository, server_prices()) for repository in self.repositories]
        await seed_mission(self.repositories[0])
        await self.services[0].initialize_account('mis-1', limit_usd='1')
        self.scope = execution_scope()

    def drop_schema(self):
        with self.admin.cursor() as cursor:
            cursor.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    async def reserve(self, service, operation='model-call-1', tokens=500_000):
        return await service.reserve(self.scope, operation, provider='provider', model='model',
                                     max_input_tokens=tokens, max_output_tokens=0)

    async def test_two_connections_cannot_both_reserve_the_last_mission_funds(self):
        self.assertNotEqual(self.connections[0].get_server_pid(), self.connections[1].get_server_pid())
        results = await asyncio.gather(self.reserve(self.services[0], 'call-a', 700_000),
                                       self.reserve(self.services[1], 'call-b', 700_000), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, BudgetExhausted) for value in results), 1)
        account = await MissionBudgetRepository.from_mission_transaction(self.repositories[1]).account('mis-1')
        self.assertEqual(account.held_nano_usd, 700_000_000)
        self.assertEqual(await self.connections[0].fetchval('SELECT COUNT(*) FROM mission_budget_reservations'), 1)

    async def test_two_connections_dispatch_once_and_settle_idempotently(self):
        reservation = await self.reserve(self.services[0])
        claims = await asyncio.gather(*(service.dispatch_once(self.scope, reservation.id, request_digest=DIGEST)
                                        for service in self.services))
        self.assertEqual(sum(value.claimed for value in claims), 1)
        results = await asyncio.gather(*(service.settle_trusted(reservation.id, trusted_usage()) for service in self.services))
        self.assertEqual(results[0], results[1])
        self.assertEqual(await self.connections[0].fetchval('SELECT COUNT(*) FROM mission_budget_receipts'), 1)
        self.assertEqual(await self.connections[1].fetchval('SELECT spent_nano_usd FROM mission_budget_accounts'), 100_000_000)

    async def test_failure_after_hold_rolls_back_before_other_connection_reserves(self):
        original = MissionBudgetRepository.insert

        async def fail_reservation(repository, value):
            if type(value).__name__ == 'BudgetReservation':
                raise RuntimeError('reservation insert failed')
            await original(repository, value)

        with patch.object(MissionBudgetRepository, 'insert', fail_reservation), self.assertRaisesRegex(RuntimeError, 'insert failed'):
            await self.reserve(self.services[0])
        reservation = await self.reserve(self.services[1], tokens=1_000_000)
        self.assertEqual(reservation.reserved_nano_usd, 1_000_000_000)

    async def test_same_contract_id_new_revision_cannot_reuse_old_account_but_late_settle_can(self):
        reservation = await self.reserve(self.services[0])
        await self.services[0].dispatch_once(self.scope, reservation.id, request_digest=DIGEST)
        await self.services[0].mark_unknown(reservation.id, reason='timeout')
        contract = await self.repositories[0].get_contract('contract-1', 1)
        await self.repositories[0].add_contract(contract.model_copy(update={'version': 2}))
        await self.connections[0].execute("UPDATE missions SET contract_version=2")
        with self.assertRaises(BudgetConflict):
            await self.reserve(self.services[1], 'call-under-revision-2')
        settled = await self.services[1].settle_trusted(reservation.id, trusted_usage())
        self.assertEqual(settled.actual_nano_usd, 100_000_000)
        self.assertEqual(await self.connections[1].fetchval('SELECT contract_version FROM mission_budget_accounts'), 1)
