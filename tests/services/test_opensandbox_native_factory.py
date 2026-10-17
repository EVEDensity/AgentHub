"""Explicit capability/profile admission before any sandbox or model I/O."""
import copy
import unittest
from unittest.mock import AsyncMock

from app.services.harness_service import HarnessError, HarnessExecutionContext, HarnessRequest
from app.services.runner.opensandbox_native import BudgetedOpenSandboxHarnessFactory, sandbox_capability_scope
from app.services.runner_protocols import ClaimedWorkResolutionError
from tests.services.test_strict_budget_harness import claimed_context, operator_profile


def context():
    value = claimed_context()
    value['contract']['repositoryScopes'] = []
    value['contract']['allowedCapabilities'] = [{'capability': 'sandbox.execute', 'scope': sandbox_capability_scope()}]
    value['workUnit']['requiredCapabilities'] = ['sandbox.execute']
    return value


class OpenSandboxNativeFactoryTests(unittest.IsolatedAsyncioTestCase):
    def factory(self):
        self.backend, self.budget = AsyncMock(), AsyncMock()
        return BudgetedOpenSandboxHarnessFactory(runner_id='runner-1', backend=self.backend,
            budget_service=self.budget, profile=operator_profile(), api_key='operator-model-key')

    def test_building_a_leased_attempt_does_not_create_or_spend(self):
        harness = self.factory().build(context())
        self.assertTrue(callable(harness.execute))
        self.backend.create.assert_not_awaited()
        self.budget.initialize_account.assert_not_awaited()

    def test_grant_must_bind_exact_profile_image_and_required_capability(self):
        cases = [([], ['sandbox.execute']),
                 ([{'capability': 'sandbox.execute', 'scope': {}}], ['sandbox.execute']),
                 ([{'capability': 'sandbox.execute', 'scope': {'profile': 'other', 'image': 'mutable:latest'}}], ['sandbox.execute']),
                 ([{'capability': 'sandbox.execute', 'scope': sandbox_capability_scope()}], []),
                 ([{'capability': 'sandbox.execute', 'scope': sandbox_capability_scope()}], ['sandbox.execute', 'repository.write'])]
        for grants, required in cases:
            value = context()
            value['contract']['allowedCapabilities'], value['workUnit']['requiredCapabilities'] = grants, required
            with self.subTest(required=required, grants=grants), self.assertRaises(ClaimedWorkResolutionError):
                self.factory().build(value)
            self.backend.create.assert_not_awaited()

    def test_claim_and_contract_identity_are_validated_before_backend_access(self):
        for path, change in [('mission', {'contractVersion': 2}), ('workUnit', {'attempt': 0}),
                             ('mission', {'status': 'CANCELLED'}), ('workUnit', {'assignedAdapter': 'a2a.outbound'})]:
            value = copy.deepcopy(context())
            value[path].update(change)
            with self.subTest(path=path, change=change), self.assertRaises(ClaimedWorkResolutionError):
                self.factory().build(value)
            self.backend.create.assert_not_awaited()

    def test_host_repository_grants_cannot_be_interpreted_as_sandbox_mounts(self):
        value = context()
        value['contract']['repositoryScopes'] = [{'repository': 'owner/repo', 'baseRef': 'main', 'paths': ['src/**']}]
        with self.assertRaisesRegex(ClaimedWorkResolutionError, 'host repository'):
            self.factory().build(value)
        self.backend.create.assert_not_awaited()

    async def test_stream_and_over_contract_timeout_are_refused_before_sandbox_creation(self):
        value = context()
        value['contract']['budgets']['timeSeconds'] = 10
        harness = self.factory().build(value)
        execution = HarnessExecutionContext('mis-1', 'wu-1', 1)
        for request in (HarnessRequest('task', 'text', 11, execution=execution),
                        HarnessRequest('task', 'text', 5, execution=execution, on_text_delta=lambda _: None)):
            with self.subTest(request=request), self.assertRaises(HarnessError):
                await harness.execute(request)
        self.backend.create.assert_not_awaited()
