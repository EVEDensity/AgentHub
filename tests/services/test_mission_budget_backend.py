"""Real Neon adapter is refused before ledger SQL, even inside its fake transaction."""
from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from app.db.mission_budget_capability import UnsupportedBudgetBackendError
from app.db.neon_http import NeonHttpConnection
from app.repositories import MissionRepository
from app.repositories.mission_budget_repository import MissionBudgetRepository
from app.services.mission_budget_service import MissionBudgetService
from tests.services.mission_budget_fixtures import execution_scope, server_prices, trusted_usage


class MissionBudgetBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.connection = NeonHttpConnection()

    def repository(self):
        @asynccontextmanager
        async def transaction():
            async with self.connection.transaction():
                yield self.connection
        return MissionRepository(execute=self.connection.execute, fetch_one=self.connection.fetchrow,
                                 fetch_all=self.connection.fetch, transaction_factory=transaction)

    async def test_real_neon_derived_and_direct_repositories_refuse_before_any_sql(self):
        with patch('app.db.neon_http._http_query', new_callable=AsyncMock) as transport:
            with self.assertRaisesRegex(UnsupportedBudgetBackendError, 'stateless'):
                MissionBudgetRepository.from_mission_transaction(self.repository())
            with self.assertRaises(UnsupportedBudgetBackendError):
                MissionBudgetRepository(execute=self.connection.execute, fetch_one=self.connection.fetchrow)
            # Checking fetch ownership also refuses a mixed configuration.
            with self.assertRaises(UnsupportedBudgetBackendError):
                MissionBudgetRepository(execute=AsyncMock(), fetch_one=self.connection.fetchrow)
            transport.assert_not_awaited()

    async def test_all_internal_budget_entrypoints_refuse_neon_without_ledger_reads_or_writes(self):
        service = MissionBudgetService(self.repository(), server_prices())
        scope = execution_scope()
        actions = (
            ('initialize', lambda: service.initialize_account('mis-1')),
            ('reserve', lambda: service.reserve(scope, 'call', provider='provider', model='model',
                                                max_input_tokens=1, max_output_tokens=1)),
            ('dispatch', lambda: service.dispatch_once(scope, 'reservation', request_digest='sha256:' + 'a' * 64)),
            ('unknown', lambda: service.mark_unknown('reservation', reason='timeout')),
            ('release', lambda: service.release_unsubmitted('reservation')),
            ('missing-usage', lambda: service.settle_trusted('reservation', None)),
            ('settle', lambda: service.settle_trusted('reservation', trusted_usage())),
        )
        response = {'command': 'OK', 'fields': [], 'rows': []}
        with patch('app.db.neon_http._http_query', new_callable=AsyncMock, return_value=response) as transport:
            for label, action in actions:
                with self.subTest(action=label):
                    transport.reset_mock()
                    with self.assertRaises(UnsupportedBudgetBackendError):
                        await action()
                    self.assertEqual([call.args[0] for call in transport.await_args_list], ['BEGIN', 'ROLLBACK'])
