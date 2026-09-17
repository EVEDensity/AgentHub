from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.workspace_admission_service import (
    DatabaseWorkspaceClaimAdmissionPolicyResolver,
    WorkspaceClaimAdmissionDeniedError,
    WorkspaceClaimAdmissionUnavailableError,
    _lookup_workspace_claim_admission,
)


class WorkspaceAdmissionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_resolver_returns_bounded_active_tenant_policy(
        self,
    ) -> None:
        lookup = AsyncMock(
            return_value={
                "tenant_id": "tenant-1",
                "status": "active",
                "max_concurrent": "12",
            }
        )

        policy = await DatabaseWorkspaceClaimAdmissionPolicyResolver(
            lookup
        ).resolve(workspace_id=" workspace-1 ")

        self.assertEqual(policy.tenant_id, "tenant-1")
        self.assertEqual(policy.max_concurrent, 12)
        lookup.assert_awaited_once_with("workspace-1")

    async def test_zero_limit_means_unlimited(self) -> None:
        async def lookup(_workspace_id: str) -> dict[str, object]:
            return {
                "tenant_id": "tenant-1",
                "status": "active",
                "max_concurrent": "0",
            }

        policy = await DatabaseWorkspaceClaimAdmissionPolicyResolver(
            lookup
        ).resolve(workspace_id="workspace-1")

        self.assertEqual(policy.max_concurrent, 0)

    async def test_inactive_tenant_is_explicitly_denied(self) -> None:
        async def lookup(_workspace_id: str) -> dict[str, object]:
            return {
                "tenant_id": "tenant-1",
                "status": "suspended",
                "max_concurrent": "2",
            }

        with self.assertRaisesRegex(
            WorkspaceClaimAdmissionDeniedError,
            "not active",
        ):
            await DatabaseWorkspaceClaimAdmissionPolicyResolver(lookup).resolve(
                workspace_id="workspace-1"
            )

    async def test_missing_or_malformed_policy_fails_closed(self) -> None:
        async def missing(_workspace_id: str) -> None:
            return None

        with self.assertRaisesRegex(
            WorkspaceClaimAdmissionUnavailableError,
            "not configured",
        ):
            await DatabaseWorkspaceClaimAdmissionPolicyResolver(missing).resolve(
                workspace_id="workspace-1"
            )

        async def malformed(_workspace_id: str) -> dict[str, object]:
            return {
                "tenant_id": "tenant-1",
                "status": "active",
                "max_concurrent": "2.5",
            }

        with self.assertRaisesRegex(
            WorkspaceClaimAdmissionUnavailableError,
            "lookup failed",
        ):
            await DatabaseWorkspaceClaimAdmissionPolicyResolver(malformed).resolve(
                workspace_id="workspace-1"
            )

        async def unknown_status(_workspace_id: str) -> dict[str, object]:
            return {
                "tenant_id": "tenant-1",
                "status": "unknown",
                "max_concurrent": "2",
            }

        with self.assertRaisesRegex(
            WorkspaceClaimAdmissionUnavailableError,
            "lookup failed",
        ):
            await DatabaseWorkspaceClaimAdmissionPolicyResolver(
                unknown_status
            ).resolve(workspace_id="workspace-1")

    async def test_lookup_error_fails_closed(self) -> None:
        async def lookup(_workspace_id: str) -> None:
            raise ConnectionError("database unavailable")

        with self.assertRaisesRegex(
            WorkspaceClaimAdmissionUnavailableError,
            "lookup failed",
        ):
            await DatabaseWorkspaceClaimAdmissionPolicyResolver(lookup).resolve(
                workspace_id="workspace-1"
            )

    async def test_invalid_workspace_is_rejected_before_lookup(self) -> None:
        lookup = AsyncMock()
        with self.assertRaisesRegex(ValueError, "workspace_id"):
            await DatabaseWorkspaceClaimAdmissionPolicyResolver(lookup).resolve(
                workspace_id=" "
            )
        lookup.assert_not_awaited()

    async def test_database_query_merges_override_over_plan_default(self) -> None:
        fetch_one = AsyncMock(
            return_value={
                "tenant_id": "tenant-1",
                "status": "active",
                "max_concurrent": "4",
            }
        )
        with patch("app.db.session.afetch_one", fetch_one):
            row = await _lookup_workspace_claim_admission("workspace-1")

        self.assertEqual(row["max_concurrent"], "4")
        query, *args = fetch_one.await_args.args
        self.assertIn("FROM platform_workspaces AS workspace", query)
        self.assertIn("JOIN platform_tenants AS tenant", query)
        self.assertIn("LEFT JOIN platform_quota_definitions AS quota", query)
        self.assertIn("tenant.quotas_json::jsonb ? 'max_concurrent'", query)
        self.assertEqual(args, ["workspace-1"])


if __name__ == "__main__":
    unittest.main()
