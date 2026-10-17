"""Shared real-budget/Runner scenario for mock-wire tests and Linux Docker CI.

The model is an explicitly deterministic loopback HTTP fixture, not a vendor.
The caller supplies the backend: ordinary tests inject wire responses, while
the disposable Linux gate passes the actual pinned OpenSandbox HTTP client.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import HTTPException, Request

from app.core.config import ArtifactStoreSettings
from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.migrations.mission_budget import upgrade_mission_budget_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain import MissionContract, MissionSource
from app.repositories import MissionRepository
from app.services.artifact_integrity_service import ContentAddressedArtifactByteVerifier
from app.services.artifact_store_service import ContentAddressedArtifactPublisher
from app.services.auth_service import get_current_user
from app.services.default_mission_contract import build_default_task_contract
from app.services.mission_budget_service import MissionBudgetService
from app.services.model_pricing import ModelPriceTable
from app.services.runner.opensandbox_native import BudgetedOpenSandboxHarnessFactory, sandbox_capability_scope
from app.services.runner_client import MissionControlRunnerClient
from app.services.runner_model_resolver import DesktopTaskClaimedWorkResolver
from app.services.runner_service import WorkUnitRunner
from app.services.strict_provider import StrictProviderProfile
from app.services.verifier_service import ControlledVerifier, MissionControlVerifierClient
from tests.api.mission_app_fixture import build_app
from tests.api.test_missions_api import FakeRunnerWorkspaceGrantAuthorizer, FakeVerifierWorkspaceGrantAuthorizer
from tests.domain.factories import build_mission, build_work_unit

MARKER = 'native sandbox marker'


class _Scenario:
    def __init__(self, root, backend, job_id):
        self.root, self.backend, self.job_id = root, backend, job_id
        self.model_calls, self.handlers = [], set()

    async def initialize(self):
        self.pool = SQLitePool(self.root / 'control.sqlite3')
        await self.pool.initialize()
        async with self.pool.acquire() as connection:
            self.connection = connection
        await _create_mission_control_plane_sqlite(connection)
        await upgrade_mission_budget_sqlite(connection)

        @asynccontextmanager
        async def transaction():
            async with connection.transaction():
                yield connection

        self.repository = MissionRepository(execute=connection.execute, fetch_one=connection.fetchrow,
                                            fetch_all=connection.fetch, transaction_factory=transaction)
        contract = build_default_task_contract('contract-1', model_cost=0.02)
        contract['allowedCapabilities'] = [{'capability': 'sandbox.execute', 'scope': sandbox_capability_scope()}]
        await self.repository.add_contract_lineage('contract-1', 'workspace-1')
        await self.repository.add_contract(MissionContract.model_validate(contract))
        await self.repository.add_mission(build_mission(status='RUNNING', source=MissionSource(type='manual')))
        await self.repository.add_work_unit(build_work_unit(kind='desktop.task', required_capabilities=['sandbox.execute'],
            assigned_agent_id='sandbox-agent', assigned_adapter='function-calling', expected_outputs=[]))
        self.store = ArtifactStoreSettings(backend='local', local_root=self.root / 'cas')
        self.byte_verifier = ContentAddressedArtifactByteVerifier(self.store)
        self.server = await asyncio.start_server(self.model_peer, '127.0.0.1', 0)

    async def close(self):
        if hasattr(self, 'server'):
            self.server.close()
            await self.server.wait_closed()
        for task in tuple(self.handlers):
            task.cancel()
        if self.handlers:
            await asyncio.gather(*self.handlers, return_exceptions=True)
        await self.pool.close()

    async def model_peer(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            headers = (await reader.readuntil(b'\r\n\r\n')).decode('ascii')
            length = next(int(line.split(':', 1)[1]) for line in headers.split('\r\n')
                          if line.lower().startswith('content-length:'))
            self.model_calls.append(json.loads(await reader.readexactly(length)))
            message = {'role': 'assistant', 'content': 'Observed the command output; byte verification remains pending.'}
            finish = 'stop'
            if len(self.model_calls) == 1:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'shell-call', 'type': 'function', 'function': {'name': 'run_shell',
                    'arguments': json.dumps({'script': "printf '%s' '" + MARKER + "'", 'timeout_seconds': 10})}}]}
                finish = 'tool_calls'
            body = json.dumps({'id': f'local-receipt-{len(self.model_calls)}', 'model': 'fixture-model',
                'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8}}).encode()
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                         + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            self.handlers.discard(task)

    def application(self):
        app = build_app(self.repository, {'id': 'runner-1', 'role': 'runner'}, artifact_byte_verifier=self.byte_verifier,
            runner_workspace_grant_authorizer=FakeRunnerWorkspaceGrantAuthorizer({('workspace-1', 'runner-1')}),
            verifier_workspace_grant_authorizer=FakeVerifierWorkspaceGrantAuthorizer({('workspace-1', 'verifier-1')}))

        async def authenticate(request: Request):
            identities = {'Bearer runner-token': ('runner-1', 'runner'), 'Bearer verifier-token': ('verifier-1', 'verifier')}
            identity = identities.get(request.headers.get('authorization'))
            if identity is None:
                raise HTTPException(status_code=401, detail='Authentication required')
            return {'id': identity[0], 'name': identity[0], 'role': identity[1]}

        app.dependency_overrides[get_current_user] = authenticate
        return app

    def factory(self):
        port = self.server.sockets[0].getsockname()[1]
        profile = StrictProviderProfile(profile_id='sandbox-native-fixture.v1', provider='fixture', model='fixture-model',
            endpoint=f'http://127.0.0.1:{port}/v1/chat/completions', max_billing_input_tokens=4096,
            max_completion_tokens=64, operator_verified_context_ceiling=True,
            context_ceiling_source='local deterministic HTTP fixture; not a charging supplier',
            context_ceiling_reviewed_at='2026-10-18', timeout_seconds=30)
        prices = ModelPriceTable('fixture.v1', {('fixture', 'fixture-model'): ('1', '2')})
        return BudgetedOpenSandboxHarnessFactory(runner_id='runner-1', backend=self.backend,
            budget_service=MissionBudgetService(self.repository, prices), profile=profile, api_key='fixture-model-key',
            job_id=self.job_id)

    async def execute(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application())) as http:
            control = MissionControlRunnerClient('http://control.test', access_token='runner-token', http_client=http)
            resolver = DesktopTaskClaimedWorkResolver(control, runner_id='runner-1', harness_factory=self.factory())
            runner = WorkUnitRunner(control, publisher=ContentAddressedArtifactPublisher(self.store), runner_id='runner-1',
                assigned_agent_id='sandbox-agent', assigned_adapter='function-calling', claimed_work_resolver=resolver,
                supported_work_unit_kinds=('desktop.task',), supported_capabilities=('sandbox.execute',),
                heartbeat_interval_seconds=0.1)
            result = await runner.claim_ready_and_run('workspace-1')
        assert result.run_result and result.run_result.success, 'actual Runner did not complete'
        assert (await self.repository.get_work_unit('wu-1')).status.value == 'VERIFYING'
        assert (await self.repository.get_mission('mis-1')).status.value == 'RUNNING'
        return await self.observations()

    async def observations(self):
        artifacts = await self.repository.list_artifacts('mis-1')
        reports = [item for item in artifacts if item.kind.value == 'report']
        assert len(artifacts) == 2 and len(reports) == 1, 'unexpected execution bundle'
        report = json.loads((self.store.local_root / 'sha256' / reports[0].digest[7:]).read_bytes())
        assert report['cleanupConfirmed'] is True, 'cleanup was not confirmed before publication'
        assert len(report['commands']) == 1 and report['commands'][0]['exitCode'] == 0
        assert MARKER in report['commands'][0]['combinedOutput'], 'real command output missing'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application())) as http:
            verifier = ControlledVerifier(MissionControlVerifierClient('http://control.test', access_token='verifier-token',
                http_client=http), byte_verifier=self.byte_verifier, verifier_id='verifier-1', verifier_version='integrity.v1')
            await verifier.discover_and_verify('workspace-1')
        mission = await self.repository.get_mission('mis-1')
        evidence = await self.repository.list_evidence('mis-1')
        account = await self.connection.fetchrow('SELECT spent_nano_usd,held_nano_usd FROM mission_budget_accounts')
        assert mission.status.value == 'SUCCEEDED' and [item.verdict.value for item in evidence] == ['PASS']
        assert len(self.model_calls) == 2 and account['spent_nano_usd'] == 22_000 and account['held_nano_usd'] == 0
        return {'terminal_status': mission.status.value, 'evidence_verdict': evidence[0].verdict.value,
            'sandbox_ids': [report['sandboxId']], 'artifacts': len(artifacts), 'model_http_requests': len(self.model_calls),
            'spent_nano_usd': account['spent_nano_usd']}


async def exercise_budgeted_roundtrip(backend, job_id):
    """Never creates a fake backend: the caller chooses test wire or real Linux."""
    with tempfile.TemporaryDirectory(prefix='native-roundtrip-') as directory:
        scenario = _Scenario(Path(directory), backend, job_id)
        try:
            await scenario.initialize()
            return await scenario.execute()
        finally:
            await scenario.close()
