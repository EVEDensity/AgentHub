"""Actual claim admission and dedicated scoped network composition."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.harness_types import (
    HarnessError,
    HarnessExecutionContext,
    HarnessRequest,
)
from app.services.runner.browser_native import (
    BudgetedBrowserReadHarnessFactory,
    browser_capability_scope,
)
from app.services.runner_protocols import ClaimedWorkResolutionError
from tests.services.test_browser_read_harness import FixtureBackend
from tests.services.test_browser_read_profile import profile_fixture
from tests.services.test_strict_budget_harness import claimed_context, operator_profile


class BrowserFactoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = profile_fixture(Path(temporary.name).resolve())
        self.backend = FixtureBackend(self.profile)

    def context(self):
        value = claimed_context()
        value["contract"]["repositoryScopes"] = []
        value["contract"]["allowedCapabilities"] = [
            {
                "capability": "browser.read",
                "scope": browser_capability_scope(self.profile),
            }
        ]
        value["workUnit"]["requiredCapabilities"] = ["browser.read"]
        return value

    def factory(self):
        self.budget = AsyncMock()
        return BudgetedBrowserReadHarnessFactory(
            runner_id="runner-1",
            backend=self.backend,
            budget_service=self.budget,
            profile=operator_profile(),
            api_key="operator-fixture-key",
        )

    def test_leased_factory_does_not_start_browser_or_reserve_money(self):
        model = object()
        with patch(
            "app.services.runner.browser_native.BudgetedModelPort", return_value=model
        ) as constructor:
            harness = self.factory().build(self.context())
        self.assertTrue(callable(harness.execute))
        self.assertEqual(self.backend.calls, [])
        self.budget.initialize_account.assert_not_awaited()
        scope = constructor.call_args.args[0]
        self.assertEqual(
            (
                scope.mission_id,
                scope.work_unit_id,
                scope.attempt,
                scope.lease_id,
                scope.runner_id,
            ),
            ("mis-1", "wu-1", 1, "lease-1", "runner-1"),
        )

    def test_scope_and_required_capability_must_match_before_model_or_browser_access(
        self,
    ):
        for scope in (
            {},
            {"profile": "other"},
            self.profile.scope() | {"origins": ["https://other.com"]},
        ):
            value = self.context()
            value["contract"]["allowedCapabilities"][0]["scope"] = scope
            with (
                self.subTest(scope=scope),
                self.assertRaises(ClaimedWorkResolutionError),
            ):
                self.factory().build(value)
        for required in ([], ["browser.read", "repository.write"]):
            value = self.context()
            value["workUnit"]["requiredCapabilities"] = required
            with (
                self.subTest(required=required),
                self.assertRaises(ClaimedWorkResolutionError),
            ):
                self.factory().build(value)
        self.assertEqual(self.backend.calls, [])

    def test_contract_lease_identity_checkpoint_and_host_repository_are_refused(self):
        changes = (
            ("mission", {"status": "CANCELLED"}),
            ("mission", {"contractVersion": 2}),
            ("workUnit", {"attempt": 0}),
            ("workUnit", {"assignedAdapter": "a2a.outbound"}),
            (
                "contract",
                {
                    "repositoryScopes": [
                        {
                            "repository": "owner/repo",
                            "baseRef": "main",
                            "paths": ["src/**"],
                        }
                    ]
                },
            ),
        )
        for key, update in changes:
            value = copy.deepcopy(self.context())
            value[key].update(update)
            with (
                self.subTest(update=update),
                self.assertRaises(ClaimedWorkResolutionError),
            ):
                self.factory().build(value)
        with self.assertRaises(ClaimedWorkResolutionError):
            self.factory().build(self.context() | {"checkpoint": {"id": "resume"}})

    async def test_actual_contract_deadline_precedes_browser_creation(self):
        value = self.context()
        value["contract"]["budgets"]["timeSeconds"] = 1
        harness = self.factory().build(value)
        with self.assertRaises(HarnessError):
            await harness.execute(
                HarnessRequest(
                    "read",
                    "text",
                    2,
                    execution=HarnessExecutionContext("mis-1", "wu-1", 1),
                )
            )
        self.assertEqual(self.backend.calls, [])
