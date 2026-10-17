"""Real Runner/HTTP/ledger/CAS/Verifier flow with a deterministic provider fixture.

This proves protocol composition and output integrity, not live vendor billing
or repository test correctness. The provider is a real loopback TCP HTTP server.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import HTTPException, Request

from app.core.config import ArtifactStoreSettings
from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.migrations.mission_budget import upgrade_mission_budget_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain import ActorRef, CapabilityGrant, MissionContract, MissionSource
from app.repositories import MissionRepository
from app.services.artifact_integrity_service import ContentAddressedArtifactByteVerifier
from app.services.artifact_store_service import ContentAddressedArtifactPublisher
from app.services.auth_service import get_current_user
from app.services.default_mission_contract import build_default_task_contract
from app.services.harness_service import FunctionTool
from app.services.mission_budget_service import MissionBudgetService
from app.services.mission_service import MissionService
from app.services.model_pricing import ModelPriceTable
from app.services.runner.strict_budget import StrictBudgetHarnessFactory
from app.services.runner_client import MissionControlRunnerClient
from app.services.runner_model_resolver import DesktopTaskClaimedWorkResolver
from app.services.runner_service import RunnerError, WorkUnitRunner
from app.services.strict_provider import StrictProviderProfile
from app.services.verifier_service import ControlledVerifier, MissionControlVerifierClient
from tests.api.mission_app_fixture import build_app
from tests.api.test_missions_api import (
    FakeRunnerWorkspaceGrantAuthorizer, FakeVerifierWorkspaceGrantAuthorizer,
)
from tests.domain.factories import build_mission, build_work_unit


class StrictBudgetRunnerSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        pool = SQLitePool(self.root / 'control.sqlite3')
        await pool.initialize()
        self.addAsyncCleanup(pool.close)
        async with pool.acquire() as connection:
            self.connection = connection
        await _create_mission_control_plane_sqlite(connection)
        await upgrade_mission_budget_sqlite(connection)

        @asynccontextmanager
        async def transaction():
            async with connection.transaction():
                yield connection

        self.repository = MissionRepository(execute=connection.execute, fetch_one=connection.fetchrow,
                                            fetch_all=connection.fetch, transaction_factory=transaction)
        self.store = ArtifactStoreSettings(backend='local', local_root=self.root / 'cas')
        self.byte_verifier = ContentAddressedArtifactByteVerifier(self.store)
        self.prices = ModelPriceTable('fixture-prices.v1', {('fixture', 'fixture-model'): ('1', '2')})
        self.budget = MissionBudgetService(self.repository, self.prices)
        self.calls, self.dispatch_observations, self.tool_calls = [], [], []
        self.cancel_before_response = False
        self.handlers = set()
        self.server = await asyncio.start_server(self.handle_provider, '127.0.0.1', 0)
        self.addAsyncCleanup(self.close_provider)
        port = self.server.sockets[0].getsockname()[1]
        self.profile = StrictProviderProfile(profile_id='integration-fixture.v1', provider='fixture',
            model='fixture-model', endpoint=f'http://127.0.0.1:{port}/v1/chat/completions',
            max_billing_input_tokens=4096, max_completion_tokens=64,
            context_ceiling_source='deterministic loopback test fixture; not a live vendor model',
            context_ceiling_reviewed_at='2026-10-18', operator_verified_context_ceiling=True)

    async def close_provider(self):
        self.server.close()
        await self.server.wait_closed()
        for task in tuple(self.handlers):
            task.cancel()
        if self.handlers:
            await asyncio.gather(*self.handlers, return_exceptions=True)

    async def handle_provider(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            headers = (await reader.readuntil(b'\r\n\r\n')).decode('ascii')
            length = next(int(line.split(':', 1)[1]) for line in headers.split('\r\n')
                          if line.lower().startswith('content-length:'))
            payload = json.loads(await reader.readexactly(length))
            self.calls.append(payload)
            self.dispatch_observations.append(await self.connection.fetchval(
                "SELECT COUNT(*) FROM mission_budget_reservations WHERE state='DISPATCHED'"))
            if self.cancel_before_response:
                await MissionService(self.repository).cancel_mission('mis-1', actor=ActorRef(type='human', id='owner'))
            if len(self.calls) == 1 and not self.cancel_before_response:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'read-call', 'type': 'function',
                    'function': {'name': 'read_candidate', 'arguments': '{}'},
                }]}
                finish = 'tool_calls'
            else:
                message, finish = {'role': 'assistant', 'content': 'Fixture report: candidate was read.'}, 'stop'
            body = json.dumps({'id': f'fixture-receipt-{len(self.calls)}', 'object': 'chat.completion',
                'model': 'fixture-model', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8}}).encode()
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                         + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            self.handlers.discard(task)

    async def seed(self, *, limit=0.02):
        contract = MissionContract.model_validate(build_default_task_contract('contract-1', model_cost=limit))
        contract = contract.model_copy(update={'allowed_capabilities': (CapabilityGrant(capability='repository.read'),)})
        await self.repository.add_contract_lineage(contract.id, 'workspace-1')
        await self.repository.add_contract(contract)
        await self.repository.add_mission(build_mission(status='RUNNING', source=MissionSource(type='manual')))
        await self.repository.add_work_unit(build_work_unit(kind='desktop.task', required_capabilities=['repository.read'],
            assigned_agent_id='strict-agent', assigned_adapter='function-calling', expected_outputs=[]))

    def application(self):
        app = build_app(self.repository, {'id': 'runner-1', 'role': 'runner'}, artifact_byte_verifier=self.byte_verifier,
            runner_workspace_grant_authorizer=FakeRunnerWorkspaceGrantAuthorizer({('workspace-1', 'runner-1')}),
            verifier_workspace_grant_authorizer=FakeVerifierWorkspaceGrantAuthorizer({('workspace-1', 'verifier-1')}))

        async def authenticate(request: Request):
            tokens = {'Bearer runner-token': ('runner-1', 'runner'), 'Bearer verifier-token': ('verifier-1', 'verifier')}
            identity = tokens.get(request.headers.get('authorization'))
            if identity is None:
                raise HTTPException(status_code=401, detail='Authentication required')
            return {'id': identity[0], 'name': identity[0], 'role': identity[1]}

        app.dependency_overrides[get_current_user] = authenticate
        return app

    def factory(self):
        async def read_candidate(_arguments):
            self.tool_calls.append('read_candidate')
            return 'Actual operator-injected local read result.'

        return StrictBudgetHarnessFactory(runner_id='runner-1', budget_service=self.budget,
            profile=self.profile, api_key='fixture-only-provider-secret', max_iterations=1,
            tools=(FunctionTool('read_candidate', read_candidate, lambda value: value),),
            allowed_tool_names=frozenset({'read_candidate'}), tool_capabilities={'read_candidate': 'repository.read'})

    async def execute(self, *, heartbeat_seconds=0.05):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application())) as http:
            control = MissionControlRunnerClient('http://control.test', access_token='runner-token', http_client=http)
            resolver = DesktopTaskClaimedWorkResolver(control, runner_id='runner-1', harness_factory=self.factory())
            runner = WorkUnitRunner(control, publisher=ContentAddressedArtifactPublisher(self.store), runner_id='runner-1',
                assigned_agent_id='strict-agent', assigned_adapter='function-calling', claimed_work_resolver=resolver,
                supported_work_unit_kinds=('desktop.task',), supported_capabilities=('repository.read',),
                heartbeat_interval_seconds=heartbeat_seconds)
            return await runner.claim_ready_and_run('workspace-1')

    async def verify(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application())) as http:
            verifier = ControlledVerifier(MissionControlVerifierClient('http://control.test', access_token='verifier-token',
                http_client=http), byte_verifier=self.byte_verifier, verifier_id='verifier-1', verifier_version='integrity.v1')
            await verifier.discover_and_verify('workspace-1')

    async def test_tool_and_final_summary_charge_before_output_integrity_acceptance(self):
        await self.seed()
        result = await self.execute()
        self.assertTrue(result.run_result.success)
        self.assertEqual((await self.repository.get_work_unit('wu-1')).status.value, 'VERIFYING')
        self.assertEqual((await self.repository.get_mission('mis-1')).status.value, 'RUNNING')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.dispatch_observations, [1, 1])
        self.assertEqual(self.tool_calls, ['read_candidate'])
        for payload in self.calls:
            self.assertEqual((payload['model'], payload['stream'], payload['n'], payload['max_completion_tokens']),
                             ('fixture-model', False, 1, 64))
        self.assertEqual(self.calls[-1].get('tool_choice', 'none'), 'none')
        self.assertNotIn('tools', self.calls[-1])
        rows = await self.connection.fetch('SELECT operation_id,state FROM mission_budget_reservations ORDER BY operation_id')
        self.assertEqual([(row['operation_id'], row['state']) for row in rows],
            [('harness:model:1:response', 'SETTLED'), ('harness:model:2:summary', 'SETTLED')])
        account = await self.connection.fetchrow('SELECT held_nano_usd,spent_nano_usd FROM mission_budget_accounts')
        self.assertEqual((account['held_nano_usd'], account['spent_nano_usd']), (0, 22_000))
        artifacts = await self.repository.list_artifacts('mis-1')
        self.assertEqual(len(artifacts), 1)
        output = (self.store.local_root / 'sha256' / artifacts[0].digest[7:]).read_bytes()
        receipts = await self.connection.fetch('SELECT document FROM mission_budget_receipts')
        self.assertNotIn(b'fixture-only-provider-secret', output)
        self.assertNotIn('fixture-only-provider-secret', str(receipts))
        await self.verify()
        self.assertEqual((await self.repository.get_mission('mis-1')).status.value, 'SUCCEEDED')
        self.assertEqual([item.verdict.value for item in await self.repository.list_evidence('mis-1')], ['PASS'])

    async def test_insufficient_summary_reservation_sends_no_second_http_request(self):
        await self.seed(limit=0.00423)
        result = await self.execute()
        self.assertFalse(result.run_result.success)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((await self.repository.get_work_unit('wu-1')).status.value, 'FAILED')
        account = await self.connection.fetchrow('SELECT held_nano_usd,spent_nano_usd FROM mission_budget_accounts')
        self.assertEqual((account['held_nano_usd'], account['spent_nano_usd']), (0, 11_000))
        self.assertEqual(await self.repository.list_artifacts('mis-1'), [])
        self.assertEqual(await self.repository.list_evidence('mis-1'), [])

    async def test_trusted_usage_after_mission_cancel_cannot_revive_business_state(self):
        await self.seed()
        self.cancel_before_response = True
        with self.assertRaises(RunnerError):
            # Let the actual response settle before the next heartbeat sees
            # cancellation. Artifact admission must still reject the old lease.
            await self.execute(heartbeat_seconds=5)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((await self.repository.get_mission('mis-1')).status.value, 'CANCELLED')
        self.assertEqual((await self.repository.get_work_unit('wu-1')).status.value, 'CANCELLED')
        account = await self.connection.fetchrow('SELECT held_nano_usd,spent_nano_usd FROM mission_budget_accounts')
        self.assertEqual((account['held_nano_usd'], account['spent_nano_usd']), (0, 11_000))
        self.assertEqual(await self.repository.list_artifacts('mis-1'), [])
        self.assertEqual(await self.repository.list_evidence('mis-1'), [])
