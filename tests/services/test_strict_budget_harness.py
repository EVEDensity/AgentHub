"""Private attempt binding, operator tool grants and explicit strict composition."""
from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services.harness_service import FunctionTool, HarnessError, HarnessExecutionContext, HarnessRequest, HarnessResumeInput
from app.services.model_contract import ModelResponse, ToolCall
from app.services.runner.strict_budget import StrictBudgetHarnessFactory
from app.services.runner_protocols import ClaimedWorkResolutionError
from app.services.strict_provider import StrictProviderProfile
from tests.domain.factories import build_contract, build_mission, build_work_unit


def claimed_context():
    mission = build_mission(status='RUNNING', source={'type': 'manual'})
    contract = build_contract(allowed_capabilities=[])
    unit = build_work_unit(kind='desktop.task', required_capabilities=[], status='LEASED', attempt=1,
                           assigned_agent_id='operator-executor', assigned_adapter='function-calling',
                           lease={'id': 'lease-1', 'runner_id': 'runner-1',
                                  'expires_at': datetime.now(timezone.utc) + timedelta(minutes=5)})
    return {'mission': mission.to_public_dict(), 'contract': contract.to_public_dict(), 'workUnit': unit.to_public_dict()}


def operator_profile():
    return StrictProviderProfile(profile_id='operator-test', provider='provider', model='model',
                                 endpoint='http://127.0.0.1:9999/v1/chat/completions',
                                 max_billing_input_tokens=100, max_completion_tokens=20,
                                 operator_verified_context_ceiling=True, context_ceiling_source='unit fixture attestation',
                                 context_ceiling_reviewed_at='2026-10-18')


class RecordingPort:
    def __init__(self, responses=None):
        self.responses = list(responses or [ModelResponse(content='operator response')])
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class StrictBudgetHarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = claimed_context()
        self.profile = operator_profile()
        self.scope_arguments = []
        self.port = RecordingPort()

        def model(scope, service, profile, api_key, *, limit_usd=None):
            self.scope_arguments.append((scope, service, profile, api_key, limit_usd))
            return self.port

        patcher = patch('app.services.runner.strict_budget._budgeted_model', side_effect=model)
        self.construct = patcher.start()
        self.addCleanup(patcher.stop)
        self.service = object()  # A typed real service is exercised by the Runner/HTTP/SQLite E2E.

    def factory(self, **updates):
        return StrictBudgetHarnessFactory(**{'runner_id': 'runner-1', 'budget_service': self.service,
                                            'profile': self.profile, 'api_key': 'unit-fixture-key', **updates})

    def request(self, **updates):
        return HarnessRequest(**{'code': 'Finish the operator task', 'language': 'text', 'timeout': 5,
                                 'execution': HarnessExecutionContext('mis-1', 'wu-1', 1), **updates})

    async def test_leased_factory_binds_private_scope_and_executes_without_streaming_or_secret_metadata(self):
        harness = self.factory().build(self.context)
        self.assertEqual(self.port.requests, [])
        scope, service, profile, key, limit = self.scope_arguments[0]
        self.assertEqual((scope.mission_id, scope.work_unit_id, scope.attempt, scope.lease_id, scope.runner_id),
                         ('mis-1', 'wu-1', 1, 'lease-1', 'runner-1'))
        self.assertIs(service, self.service)
        self.assertIs(profile, self.profile)
        self.assertEqual(key, 'unit-fixture-key')
        self.assertIsNone(limit)
        result = await harness.execute(self.request())
        self.assertTrue(result.sandbox.success)
        request = self.port.requests[0]
        self.assertFalse(request.stream)
        self.assertEqual(request.metadata['modelOperationId'], 'harness:model:1:response')
        self.assertNotIn('leaseId', request.metadata)
        self.assertNotIn('runnerId', request.metadata)
        self.assertNotIn('unit-fixture-key', str(harness.recovery_manifest))
        self.assertFalse(harness.recovery_manifest['resumeSupported'])

    def test_context_identity_lease_contract_and_resume_validation_refuses_before_model_construction(self):
        changes = (
            ('mission', 'id', 'another'), ('mission', 'status', 'CANCELLED'),
            ('contract', 'version', 2), ('workUnit', 'attempt', 0),
            ('workUnit', 'assignedAdapter', 'a2a.outbound'), ('workUnit', 'assignedAgentId', None),
            ('workUnit', 'kind', 'mission.fork'), ('workUnit', 'parentWorkUnitId', 'parent'),
        )
        for section, field, value in changes:
            context = copy.deepcopy(self.context)
            context[section][field] = value
            with self.subTest(section=section, field=field), self.assertRaises(ClaimedWorkResolutionError):
                self.factory().build(context)
        for lease_update in ({'runnerId': 'other'}, {'expiresAt': '2000-01-01T00:00:00Z'}):
            context = copy.deepcopy(self.context)
            context['workUnit']['lease'].update(lease_update)
            with self.subTest(lease=lease_update), self.assertRaises(ClaimedWorkResolutionError):
                self.factory().build(context)
        with self.assertRaises(ClaimedWorkResolutionError):
            self.factory().build({**self.context, 'checkpoint': {'id': 'old-model-call'}})
        self.construct.assert_not_called()

    async def test_execution_cannot_change_attempt_or_enable_resume_stream_or_extra_time(self):
        harness = self.factory().build(self.context)
        invalid = (
            {'execution': None}, {'execution': HarnessExecutionContext('mis-1', 'wu-1', 2)},
            {'execution': HarnessExecutionContext('other', 'wu-1', 1)},
            {'resume': HarnessResumeInput('checkpoint', 1)}, {'on_text_delta': lambda _text: None},
            {'timeout': 4000}, {'timeout': float('inf')},
        )
        for update in invalid:
            with self.subTest(update=update), self.assertRaises(HarnessError):
                await harness.execute(self.request(**update))
        self.assertEqual(self.port.requests, [])

    async def test_tool_requires_operator_allowlist_and_actual_contract_grant_and_required_capability(self):
        called = []

        async def read(_arguments):
            called.append('read')
            return 'local bytes'

        tool = FunctionTool('read_candidate', read, lambda value: value)
        options = {'tools': (tool,), 'allowed_tool_names': frozenset({'read_candidate'}),
                   'tool_capabilities': {'read_candidate': 'repository.read'}}
        with self.assertRaises(ValueError):
            self.factory(tools=(tool,))
        context = copy.deepcopy(self.context)
        with self.assertRaises(ClaimedWorkResolutionError):
            self.factory(**options).build(context)
        context['workUnit']['requiredCapabilities'] = ['repository.read']
        with self.assertRaises(ClaimedWorkResolutionError):
            self.factory(**options).build(context)
        context['contract']['allowedCapabilities'] = [{'capability': 'repository.read', 'scope': {'path': 'app/**'}}]
        with self.assertRaises(ClaimedWorkResolutionError):
            self.factory(**options).build(context)
        context['contract']['allowedCapabilities'][0]['scope'] = {}
        self.port = RecordingPort([ModelResponse(tool_calls=(ToolCall('read-1', 'read_candidate', {}),)),
                                   ModelResponse(content='summary')])
        harness = self.factory(**options, max_iterations=1).build(context)
        result = await harness.execute(self.request())
        self.assertTrue(result.sandbox.success)
        self.assertEqual(called, ['read'])
        self.assertEqual([request.metadata['modelOperationId'] for request in self.port.requests],
                         ['harness:model:1:response', 'harness:model:2:summary'])
        self.assertEqual(self.port.requests[1].tools, ())

    def test_known_model_and_delegation_tools_are_not_part_of_this_profile(self):
        async def local(_arguments):
            return 'not invoked'

        for name, capability in (('delegate_to_agent', 'repository.read'), ('read_candidate', 'model.invoke'),
                                 ('read_candidate', 'a2a.send'), ('read_candidate', 'mcp.call')):
            tool = FunctionTool(name, local, lambda value: value)
            with self.subTest(name=name, capability=capability), self.assertRaises(ValueError):
                self.factory(tools=(tool,), allowed_tool_names=frozenset({name}), tool_capabilities={name: capability})

    async def test_strict_composition_does_not_retry_transient_error_text(self):
        self.port = RecordingPort([RuntimeError('HTTP 503 uncertain request'), ModelResponse(content='must not run')])
        result = await self.factory().build(self.context).execute(self.request())
        self.assertFalse(result.sandbox.success)
        self.assertEqual(len(self.port.requests), 1)

    async def test_operator_limit_is_privately_bound_and_cannot_come_from_context(self):
        self.factory().build({**self.context, 'limitUsd': '999'})
        self.assertIsNone(self.scope_arguments[-1][-1])
        harness = self.factory(limit_usd='0.1').build(self.context)
        self.assertEqual(self.scope_arguments[-1][-1], '0.1')
        await harness.execute(self.request())
        self.assertNotIn('limitUsd', self.port.requests[-1].metadata)
