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

from app.api.v1.missions import get_mission_repository, router
from app.db.migrations.mission_control_plane import (
    AGENT_BINDING_PERSISTENCE_UPGRADE,
    DELEGATION_PERSISTENCE_UPGRADE,
    MISSION_CONTROL_PLANE_UPGRADE,
    MISSION_EVENT_LEDGER_UPGRADE,
    WORK_UNIT_PERSISTENCE_UPGRADE,
)
from app.repositories import MissionRepository
from app.services.auth_service import get_current_user
from app.services.runner_service import MissionControlRunnerClient
from tests.domain.factories import build_contract, build_mission, build_work_unit

_POSTGRES_DSN = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")
_MIGRATIONS = (
    MISSION_CONTROL_PLANE_UPGRADE
    + MISSION_EVENT_LEDGER_UPGRADE
    + WORK_UNIT_PERSISTENCE_UPGRADE
    + DELEGATION_PERSISTENCE_UPGRADE
    + AGENT_BINDING_PERSISTENCE_UPGRADE
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
    ):
        selection = await super().get_workspace_bound_work_unit_for_claim(
            workspace_id,
            agent_id=agent_id,
            adapter_type=adapter_type,
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


def _build_app(repository: MissionRepository) -> FastAPI:
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_mission_repository] = lambda: repository
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
        transport = httpx.ASGITransport(app=_build_app(self._repository))
        async with (
            httpx.AsyncClient(transport=transport) as http_a,
            httpx.AsyncClient(transport=transport) as http_b,
        ):
            control_a = MissionControlRunnerClient(
                "http://mission-control.test",
                access_token="workspace-1",
                http_client=http_a,
            )
            control_b = MissionControlRunnerClient(
                "http://mission-control.test",
                access_token="workspace-1",
                http_client=http_b,
            )
            claims = await asyncio.wait_for(
                asyncio.gather(
                    control_a.claim_ready_work_unit(
                        "workspace-1",
                        runner_id="workspace-1",
                        agent_id="reviewer",
                        adapter_type="local_codex",
                        lease_seconds=120,
                    ),
                    control_b.claim_ready_work_unit(
                        "workspace-1",
                        runner_id="workspace-1",
                        agent_id="reviewer",
                        adapter_type="local_codex",
                        lease_seconds=120,
                    ),
                ),
                timeout=10,
            )
            empty = await control_a.claim_ready_work_unit(
                "workspace-1",
                runner_id="workspace-1",
                agent_id="reviewer",
                adapter_type="local_codex",
                lease_seconds=120,
            )

        claimed_units = [claim["workUnit"] for claim in claims]
        self.assertTrue(all(unit is not None for unit in claimed_units))
        self.assertCountEqual(
            [unit["id"] for unit in claimed_units],
            ["work-a", "work-b"],
        )
        self.assertEqual(
            {unit["lease"]["runnerId"] for unit in claimed_units},
            {"workspace-1"},
        )
        self.assertIsNone(empty["workUnit"])


if __name__ == "__main__":
    unittest.main()
