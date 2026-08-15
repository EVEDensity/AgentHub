from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.workspace_access_service import (
    RUNNER_WORKSPACE_CLAIM_SCOPE,
    DatabaseRunnerWorkspaceGrantAuthorizer,
    RunnerWorkspaceGrantUnavailableError,
    _lookup_runner_workspace_grant,
)


class WorkspaceAccessServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_authorizer_uses_exact_workspace_principal_and_scope(
        self,
    ) -> None:
        calls: list[tuple[str, str, str]] = []

        async def lookup(
            workspace_id: str,
            principal_id: str,
            scope: str,
        ) -> dict[str, object]:
            calls.append((workspace_id, principal_id, scope))
            return {"granted": 1}

        granted = await DatabaseRunnerWorkspaceGrantAuthorizer(lookup).has_claim_grant(
            workspace_id=" workspace-1 ",
            principal_id=" runner-a ",
        )

        self.assertTrue(granted)
        self.assertEqual(
            calls,
            [("workspace-1", "runner-a", RUNNER_WORKSPACE_CLAIM_SCOPE)],
        )

    async def test_database_authorizer_denies_missing_grant(self) -> None:
        async def lookup(
            _workspace_id: str,
            _principal_id: str,
            _scope: str,
        ) -> None:
            return None

        granted = await DatabaseRunnerWorkspaceGrantAuthorizer(lookup).has_claim_grant(
            workspace_id="workspace-1",
            principal_id="runner-a",
        )

        self.assertFalse(granted)

    async def test_database_authorizer_fails_closed_on_lookup_error(self) -> None:
        async def lookup(
            _workspace_id: str,
            _principal_id: str,
            _scope: str,
        ) -> None:
            raise ConnectionError("database unavailable")

        with self.assertRaisesRegex(
            RunnerWorkspaceGrantUnavailableError,
            "lookup failed",
        ):
            await DatabaseRunnerWorkspaceGrantAuthorizer(lookup).has_claim_grant(
                workspace_id="workspace-1",
                principal_id="runner-a",
            )

    async def test_database_authorizer_rejects_invalid_identifiers_before_lookup(
        self,
    ) -> None:
        lookup = AsyncMock(return_value={"granted": 1})
        authorizer = DatabaseRunnerWorkspaceGrantAuthorizer(lookup)

        with self.assertRaisesRegex(ValueError, "workspace_id"):
            await authorizer.has_claim_grant(
                workspace_id=" ",
                principal_id="runner-a",
            )
        with self.assertRaisesRegex(ValueError, "principal_id"):
            await authorizer.has_claim_grant(
                workspace_id="workspace-1",
                principal_id=" ",
            )

        lookup.assert_not_awaited()

    async def test_database_lookup_reads_only_explicit_permission_membership(
        self,
    ) -> None:
        fetch_one = AsyncMock(return_value={"granted": 1})
        with patch("app.db.session.afetch_one", fetch_one):
            row = await _lookup_runner_workspace_grant(
                "workspace-1",
                "runner-a",
                RUNNER_WORKSPACE_CLAIM_SCOPE,
            )

        self.assertEqual(row, {"granted": 1})
        query, *args = fetch_one.await_args.args
        self.assertIn("FROM platform_workspace_members", query)
        self.assertIn("workspace_id = $1", query)
        self.assertIn("user_id = $2", query)
        self.assertIn("permissions @> jsonb_build_array($3::text)", query)
        self.assertEqual(
            args,
            ["workspace-1", "runner-a", RUNNER_WORKSPACE_CLAIM_SCOPE],
        )


if __name__ == "__main__":
    unittest.main()
