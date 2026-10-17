"""Real Chromium plus real Native/provider-HTTP/budget/Runner/CAS verification."""
from __future__ import annotations

import asyncio
import json
import os
import unittest

from app.domain import CapabilityGrant, MissionContract, MissionSource
from app.services.default_mission_contract import build_default_task_contract
from app.services.runner.browser_native import BudgetedBrowserReadHarnessFactory
from tests.domain.factories import build_mission, build_work_unit
from tests.integration import test_strict_budget_runner_sqlite as budget_fixture
from tests.integration.browser_read_fixture import BrowserDocumentFixture, real_backend


class BrowserNativeRunnerSQLiteTests(unittest.IsolatedAsyncioTestCase):
    close_provider = budget_fixture.StrictBudgetRunnerSQLiteTests.close_provider
    application = budget_fixture.StrictBudgetRunnerSQLiteTests.application
    verify = budget_fixture.StrictBudgetRunnerSQLiteTests.verify

    async def asyncSetUp(self):
        if os.environ.get('AGENTHUB_REAL_BROWSER') != '1':
            self.skipTest('Real Chromium composition requires explicit AGENTHUB_REAL_BROWSER=1')
        self.web = await BrowserDocumentFixture().start()
        self.addAsyncCleanup(self.web.close)
        self.backend = real_backend(self.web.origin)
        self.target = self.web.origin + '/document'
        await budget_fixture.StrictBudgetRunnerSQLiteTests.asyncSetUp(self)

    async def handle_provider(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            headers = (await reader.readuntil(b'\r\n\r\n')).decode('ascii')
            length = next(int(line.split(':', 1)[1]) for line in headers.split('\r\n')
                          if line.lower().startswith('content-length:'))
            self.calls.append(json.loads(await reader.readexactly(length)))
            if len(self.calls) == 1:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'browser-fixture-read', 'type': 'function', 'function': {
                        'name': 'browser_read', 'arguments': json.dumps({'url': self.target})}}]}
                finish = 'tool_calls'
            else:
                message = {'role': 'assistant', 'content': 'Fixture completion claim; host observations decide acceptance.'}
                finish = 'stop'
            data = json.dumps({'id': f'browser-fixture-{len(self.calls)}', 'object': 'chat.completion',
                'model': 'fixture-model', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8}}).encode()
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                         + str(len(data)).encode() + b'\r\nConnection: close\r\n\r\n' + data)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            self.handlers.discard(task)

    async def seed(self):
        value = build_default_task_contract('contract-1', time_seconds=60, model_cost=0.03)
        value['acceptanceCriteria'][0]['configuration'].update(minimumArtifacts=2,
            requiredArtifactKinds=['test-result', 'report'])
        contract = MissionContract.model_validate(value).model_copy(update={'allowed_capabilities': (
            CapabilityGrant(capability='browser.read', scope=self.backend.profile.scope()),)})
        await self.repository.add_contract_lineage(contract.id, 'workspace-1')
        await self.repository.add_contract(contract)
        await self.repository.add_mission(build_mission(status='RUNNING', source=MissionSource(type='manual')))
        await self.repository.add_work_unit(build_work_unit(kind='desktop.task', required_capabilities=['browser.read'],
            assigned_agent_id='strict-agent', assigned_adapter='function-calling', expected_outputs=[]))

    def factory(self):
        return BudgetedBrowserReadHarnessFactory(runner_id='runner-1', backend=self.backend,
            budget_service=self.budget, profile=self.profile, api_key='fixture-only-provider-secret', max_iterations=1)

    async def execute(self):
        import httpx
        from app.services.artifact_store_service import ContentAddressedArtifactPublisher
        from app.services.runner_client import MissionControlRunnerClient
        from app.services.runner_model_resolver import DesktopTaskClaimedWorkResolver
        from app.services.runner_service import WorkUnitRunner

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application())) as http:
            control = MissionControlRunnerClient('http://control.test', access_token='runner-token', http_client=http)
            resolver = DesktopTaskClaimedWorkResolver(control, runner_id='runner-1', harness_factory=self.factory())
            runner = WorkUnitRunner(control, publisher=ContentAddressedArtifactPublisher(self.store), runner_id='runner-1',
                assigned_agent_id='strict-agent', assigned_adapter='function-calling', claimed_work_resolver=resolver,
                supported_work_unit_kinds=('desktop.task',), supported_capabilities=('browser.read',),
                heartbeat_interval_seconds=0.05)
            return await runner.claim_ready_and_run('workspace-1')

    async def test_browser_observations_and_budget_precede_independent_integrity_acceptance(self):
        await self.seed()
        result = await self.execute()
        self.assertTrue(result.run_result.success)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((await self.repository.get_work_unit('wu-1')).status.value, 'VERIFYING')
        self.assertEqual((await self.repository.get_mission('mis-1')).status.value, 'RUNNING')
        artifacts = await self.repository.list_artifacts('mis-1')
        self.assertEqual(len(artifacts), 2)
        report_artifact = next(artifact for artifact in artifacts if artifact.kind.value == 'report')
        report = json.loads((self.store.local_root / 'sha256' / report_artifact.digest[7:]).read_bytes())
        self.assertEqual(report['acceptance'], 'artifact-integrity-only')
        self.assertTrue(report['cleanupConfirmed'])
        self.assertEqual(report['reads'][0]['result']['httpStatus'], 200)
        self.assertIn('Browser fixture 中文', report['reads'][0]['result']['text'])
        self.assertEqual(report['profileDigest'], self.backend.profile.digest)
        await self.verify()
        self.assertEqual((await self.repository.get_mission('mis-1')).status.value, 'SUCCEEDED')
        self.assertEqual([e.verdict.value for e in await self.repository.list_evidence('mis-1')], ['PASS'])
        await self.assert_account_settled()

    async def test_a_model_completion_claim_cannot_override_a_real_http_failure(self):
        await self.seed()
        self.target = self.web.origin + '/missing'
        result = await self.execute()
        self.assertFalse(result.run_result.success)
        self.assertEqual((await self.repository.get_work_unit('wu-1')).status.value, 'FAILED')
        self.assertEqual(await self.repository.list_artifacts('mis-1'), [])
        self.assertEqual(await self.repository.list_evidence('mis-1'), [])
        self.assertEqual(len(self.calls), 2)
        await self.assert_account_settled()

    async def assert_account_settled(self):
        account = await self.connection.fetchrow('SELECT held_nano_usd,spent_nano_usd FROM mission_budget_accounts')
        self.assertEqual((account['held_nano_usd'], account['spent_nano_usd']), (0, 22_000))
