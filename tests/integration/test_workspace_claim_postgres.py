from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Request

from app.api.v1.missions import (
    get_mission_repository,
    get_runner_workspace_grant_authorizer,
    get_workspace_claim_admission_policy_resolver,
    router,
)
from app.db.migrations.mission_control_plane import (
    AGENT_BINDING_PERSISTENCE_UPGRADE,
    CONTRACT_REVISION_BINDING_UPGRADE,
    DELEGATION_PERSISTENCE_UPGRADE,
    MISSION_CONTROL_PLANE_UPGRADE,
    MISSION_EVENT_LEDGER_UPGRADE,
    WORK_UNIT_PERSISTENCE_UPGRADE,
)
from app.repositories import MissionRepository
from app.services.auth_service import get_current_user
from app.services.runner_service import MissionControlRunnerClient
from app.services.workspace_access_service import (
    DatabaseRunnerWorkspaceGrantAuthorizer,
    RunnerWorkspaceGrantAuthorizer,
)
from app.services.workspace_admission_service import (
    DatabaseWorkspaceClaimAdmissionPolicyResolver,
    WorkspaceClaimAdmissionPolicyResolver,
)
from tests.domain.factories import build_contract, build_mission, build_work_unit

_POSTGRES_DSN = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")
_MIGRATIONS = (
    MISSION_CONTROL_PLANE_UPGRADE
    + MISSION_EVENT_LEDGER_UPGRADE
    + WORK_UNIT_PERSISTENCE_UPGRADE
    + DELEGATION_PERSISTENCE_UPGRADE
    + AGENT_BINDING_PERSISTENCE_UPGRADE
    # add_mission inserts missions.contract_version; the column is added by
    # this later upgrade, so the schema must include it before seeding.
    + CONTRACT_REVISION_BINDING_UPGRADE
)


class _BarrierMissionRepository(MissionRepository):
    """Hold selected rows until both concurrent claims have acquired locks."""

    def __init__(
        self,
        *,
        barrier: asyncio.Barrier,
        transaction_factory: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(transaction_factory=transaction_factory, **kwargs)
        self._barrier = barrier
        self._test_transaction_factory = transaction_factory

    @asynccontextmanager
    async def transaction(self):
        async with self._test_transaction_factory() as connection:
            yield type(self)(
                execute=connection.execute,
                fetch_one=connection.fetchrow,
                fetch_all=connection.fetch,
                transaction_factory=self._test_transaction_factory,
                barrier=self._barrier,
            )

    async def get_workspace_bound_work_unit_for_claim(
        self,
        workspace_id: str,
        *,
        agent_id: str,
        adapter_type: str,
        supported_work_unit_kinds: tuple[str, ...],
    ):
        selection = await super().get_workspace_bound_work_unit_for_claim(
            workspace_id,
            agent_id=agent_id,
            adapter_type=adapter_type,
            supported_work_unit_kinds=supported_work_unit_kinds,
        )
        if selection is not None:
            await asyncio.wait_for(self._barrier.wait(), timeout=5)
        return selection


async def _authenticated_runner(request: Request) -> dict[str, str]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"id": token, "name": token, "role": "runner"}


def _build_app(
    repository: MissionRepository,
    grant_authorizer: RunnerWorkspaceGrantAuthorizer,
    admission_policy_resolver: WorkspaceClaimAdmissionPolicyResolver,
) -> FastAPI:
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_mission_repository] = lambda: repository
    application.dependency_overrides[get_runner_workspace_grant_authorizer] = (
        lambda: grant_authorizer
    )
    application.dependency_overrides[get_workspace_claim_admission_policy_resolver] = (
        lambda: admission_policy_resolver
    )
    application.dependency_overrides[get_current_user] = _authenticated_runner
    return application


@unittest.skipUnless(
    _POSTGRES_DSN,
    "AGENTHUB_TEST_POSTGRES_DSN is required for PostgreSQL lock tests",
)
class WorkspaceClaimPostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        assert _POSTGRES_DSN is not None
        self._schema = f"agenthub_test_{uuid.uuid4().hex}"
        setup_connection = await asyncpg.connect(_POSTGRES_DSN)
        try:
            await setup_connection.execute(f'CREATE SCHEMA "{self._schema}"')
        finally:
            await setup_connection.close()
        self.addAsyncCleanup(self._drop_schema)

        self._pool = await asyncpg.create_pool(
            _POSTGRES_DSN,
            min_size=2,
            max_size=4,
            server_settings={"search_path": self._schema},
        )
        self.addAsyncCleanup(self._pool.close)
        async with self._pool.acquire() as connection:
            for statement in _MIGRATIONS:
                await connection.execute(statement)
            await connection.execute(
                """
                CREATE TABLE platform_tenants (
                    id TEXT PRIMARY KEY,
                    plan TEXT NOT NULL,
                    status TEXT NOT NULL,
                    quotas_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE platform_quota_definitions (
                    plan TEXT PRIMARY KEY,
                    max_concurrent INTEGER NOT NULL
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE platform_workspaces (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL
                )
                """
            )
            await connection.execute(
                """
                INSERT INTO platform_tenants (id, plan, status, quotas_json)
                VALUES ('tenant-1', 'test', 'active', '{}')
                """
            )
            await connection.execute(
                """
                INSERT INTO platform_quota_definitions (plan, max_concurrent)
                VALUES ('test', 0)
                """
            )
            await connection.execute(
                """
                INSERT INTO platform_workspaces (id, tenant_id)
                VALUES ('workspace-1', 'tenant-1')
                """
            )
            await connection.execute(
                """
                CREATE TABLE platform_workspace_members (
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
                    PRIMARY KEY (workspace_id, user_id)
                )
                """
            )
            await connection.executemany(
                """
                INSERT INTO platform_workspace_members (
                    workspace_id, user_id, role, permissions
                ) VALUES ($1, $2, 'runner', '["mission:claim"]'::jsonb)
                """,
                [("workspace-1", "runner-a"), ("workspace-1", "runner-b")],
            )

        @asynccontextmanager
        async def transaction_factory():
            async with (
                self._pool.acquire() as connection,
                connection.transaction(),
            ):
                yield connection

        async def execute(sql: str, *args: Any) -> None:
            async with self._pool.acquire() as connection:
                await connection.execute(sql, *args)

        async def fetch_one(sql: str, *args: Any):
            async with self._pool.acquire() as connection:
                return await connection.fetchrow(sql, *args)

        async def fetch_all(sql: str, *args: Any):
            async with self._pool.acquire() as connection:
                return await connection.fetch(sql, *args)

        self._repository = _BarrierMissionRepository(
            execute=execute,
            fetch_one=fetch_one,
            fetch_all=fetch_all,
            transaction_factory=transaction_factory,
            barrier=asyncio.Barrier(2),
        )
        self._plain_repository = MissionRepository(
            execute=execute,
            fetch_one=fetch_one,
            fetch_all=fetch_all,
            transaction_factory=transaction_factory,
        )

        async def lookup_grant(
            workspace_id: str,
            principal_id: str,
            scope: str,
        ):
            async with self._pool.acquire() as connection:
                return await connection.fetchrow(
                    """
                    SELECT 1 AS granted
                    FROM platform_workspace_members
                    WHERE workspace_id = $1
                      AND user_id = $2
                      AND permissions @> jsonb_build_array($3::text)
                    LIMIT 1
                    """,
                    workspace_id,
                    principal_id,
                    scope,
                )

        self._grant_authorizer = DatabaseRunnerWorkspaceGrantAuthorizer(
            lookup_grant
        )

        async def lookup_admission(workspace_id: str):
            async with self._pool.acquire() as connection:
                return await connection.fetchrow(
                    """
                    SELECT workspace.tenant_id,
                           tenant.status,
                           CASE
                               WHEN tenant.quotas_json::jsonb ? 'max_concurrent'
                               THEN tenant.quotas_json::jsonb ->> 'max_concurrent'
                               ELSE quota.max_concurrent::text
                           END AS max_concurrent
                    FROM platform_workspaces AS workspace
                    JOIN platform_tenants AS tenant
                      ON tenant.id = workspace.tenant_id
                    LEFT JOIN platform_quota_definitions AS quota
                      ON quota.plan = tenant.plan
                    WHERE workspace.id = $1
                    LIMIT 1
                    """,
                    workspace_id,
                )

        self._admission_policy_resolver = (
            DatabaseWorkspaceClaimAdmissionPolicyResolver(lookup_admission)
        )
        await self._seed_ready_work()

    async def _drop_schema(self) -> None:
        assert _POSTGRES_DSN is not None
        connection = await asyncpg.connect(_POSTGRES_DSN)
        try:
            await connection.execute(
                f'DROP SCHEMA IF EXISTS "{self._schema}" CASCADE'
            )
        finally:
            await connection.close()

    async def _seed_ready_work(self) -> None:
        contract = build_contract()
        await self._repository.add_contract(contract)
        for suffix in ("a", "b"):
            mission_id = f"mission-{suffix}"
            await self._repository.add_mission(
                build_mission(
                    id=mission_id,
                    contract_id=contract.id,
                    status="RUNNING",
                    source={
                        "type": "a2a.inbound",
                        "reference": "https://peer.example.test",
                        "externalId": f"remote-{suffix}",
                    },
                )
            )
            await self._repository.add_work_unit(
                build_work_unit(
                    id=f"work-{suffix}",
                    mission_id=mission_id,
                    kind="a2a.inbound",
                    required_capabilities=["a2a.receive"],
                    assigned_agent_id="reviewer",
                    assigned_adapter="local_codex",
                )
            )

    async def test_skip_locked_claims_distinct_rows_without_duplicate_lease(self) -> None:
        transport = httpx.ASGITransport(
            app=_build_app(
                self._repository,
                self._grant_authorizer,
                self._admission_policy_resolver,
            )
        )
        async with (
            httpx.AsyncClient(transport=transport) as http_a,
            httpx.AsyncClient(transport=transport) as http_b,
        ):
            control_a = MissionControlRunnerClient(
                "http://mission-control.test",
                access_token="runner-a",
                http_client=http_a,
            )
            control_b = MissionControlRunnerClient(
                "http://mission-control.test",
                access_token="runner-b",
                http_client=http_b,
            )
            claims = await asyncio.wait_for(
                asyncio.gather(
                    control_a.claim_ready_work_unit(
                        "workspace-1",
                        runner_id="runner-a",
                        agent_id="reviewer",
                        adapter_type="local_codex",
                        supported_work_unit_kinds=("a2a.inbound",),
                        lease_seconds=120,
                    ),
                    control_b.claim_ready_work_unit(
                        "workspace-1",
                        runner_id="runner-b",
                        agent_id="reviewer",
                        adapter_type="local_codex",
                        supported_work_unit_kinds=("a2a.inbound",),
                        lease_seconds=120,
                    ),
                ),
                timeout=10,
            )
            empty = await control_a.claim_ready_work_unit(
                "workspace-1",
                runner_id="runner-a",
                agent_id="reviewer",
                adapter_type="local_codex",
                supported_work_unit_kinds=("a2a.inbound",),
                lease_seconds=120,
            )

        claimed_units = [claim["workUnit"] for claim in claims]
        self.assertEqual(
            {claim["claimStatus"] for claim in claims},
            {"claimed"},
        )
        self.assertTrue(all(unit is not None for unit in claimed_units))
        self.assertCountEqual(
            [unit["id"] for unit in claimed_units],
            ["work-a", "work-b"],
        )
        self.assertEqual(
            {unit["lease"]["runnerId"] for unit in claimed_units},
            {"runner-a", "runner-b"},
        )
        self.assertIsNone(empty["workUnit"])
        self.assertEqual(empty["claimStatus"], "idle")

    async def test_kind_filter_leaves_unsupported_rows_unleased(self) -> None:
        transport = httpx.ASGITransport(
            app=_build_app(
                self._plain_repository,
                self._grant_authorizer,
                self._admission_policy_resolver,
            )
        )
        async with httpx.AsyncClient(transport=transport) as http_client:
            control = MissionControlRunnerClient(
                "http://mission-control.test",
                access_token="runner-a",
                http_client=http_client,
            )
            unsupported = await control.claim_ready_work_unit(
                "workspace-1",
                runner_id="runner-a",
                agent_id="reviewer",
                adapter_type="local_codex",
                supported_work_unit_kinds=("code_change",),
                lease_seconds=120,
            )
            supported = await control.claim_ready_work_unit(
                "workspace-1",
                runner_id="runner-a",
                agent_id="reviewer",
                adapter_type="local_codex",
                supported_work_unit_kinds=("a2a.inbound",),
                lease_seconds=120,
            )

        self.assertEqual(unsupported["claimStatus"], "idle")
        self.assertIsNone(unsupported["workUnit"])
        self.assertEqual(supported["claimStatus"], "claimed")
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT id, status, attempt FROM work_units ORDER BY id"
            )
        statuses = {row["id"]: (row["status"], row["attempt"]) for row in rows}
        self.assertEqual(statuses[supported["workUnit"]["id"]], ("LEASED", 1))
        pending = [state for work_id, state in statuses.items() if work_id != supported["workUnit"]["id"]]
        self.assertEqual(pending, [("PENDING", 0)])

        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE platform_workspace_members
                SET permissions = '[]'::jsonb
                WHERE workspace_id = $1 AND user_id = $2
                """,
                "workspace-1",
                "runner-a",
            )
        events_before_revocation = await self._repository.list_events(
            "mission-a",
            limit=100,
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://mission-control.test"
        ) as revoked_http:
            revoked = await revoked_http.post(
                "/api/v1/missions/work-unit-claims",
                json={
                    "workspaceId": "workspace-1",
                    "agentId": "reviewer",
                    "adapterType": "local_codex",
                    "supportedWorkUnitKinds": ["code_change"],
                    "leaseSeconds": 120,
                },
                headers={"Authorization": "Bearer runner-a"},
            )
        self.assertEqual(revoked.status_code, 403)
        events_after_revocation = await self._repository.list_events(
            "mission-a",
            limit=100,
        )
        self.assertEqual(events_after_revocation, events_before_revocation)

    async def test_tenant_limit_prevents_concurrent_over_admission(self) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE platform_quota_definitions
                SET max_concurrent = 1
                WHERE plan = 'test'
                """
            )
        transport = httpx.ASGITransport(
            app=_build_app(
                self._plain_repository,
                self._grant_authorizer,
                self._admission_policy_resolver,
            )
        )
        async with (
            httpx.AsyncClient(transport=transport) as http_a,
            httpx.AsyncClient(transport=transport) as http_b,
        ):
            control_a = MissionControlRunnerClient(
                "http://mission-control.test",
                access_token="runner-a",
                http_client=http_a,
            )
            control_b = MissionControlRunnerClient(
                "http://mission-control.test",
                access_token="runner-b",
                http_client=http_b,
            )
            claims = await asyncio.wait_for(
                asyncio.gather(
                    control_a.claim_ready_work_unit(
                        "workspace-1",
                        runner_id="runner-a",
                        agent_id="reviewer",
                        adapter_type="local_codex",
                        supported_work_unit_kinds=("a2a.inbound",),
                        lease_seconds=120,
                    ),
                    control_b.claim_ready_work_unit(
                        "workspace-1",
                        runner_id="runner-b",
                        agent_id="reviewer",
                        adapter_type="local_codex",
                        supported_work_unit_kinds=("a2a.inbound",),
                        lease_seconds=120,
                    ),
                ),
                timeout=10,
            )

        claimed_units = [claim["workUnit"] for claim in claims]
        self.assertEqual(sum(unit is not None for unit in claimed_units), 1)
        self.assertCountEqual(
            [claim["claimStatus"] for claim in claims],
            ["claimed", "capacity_saturated"],
        )
        async with self._pool.acquire() as connection:
            active_count = await connection.fetchval(
                """
                SELECT COUNT(*)
                FROM work_units
                WHERE status IN ('LEASED', 'RUNNING')
                """
            )
            lease_events = await connection.fetchval(
                """
                SELECT COUNT(*)
                FROM mission_events
                WHERE event_type = 'work_unit.lifecycle.leased'
                """
            )
        self.assertEqual(active_count, 1)
        self.assertEqual(lease_events, 1)


if __name__ == "__main__":
    unittest.main()
