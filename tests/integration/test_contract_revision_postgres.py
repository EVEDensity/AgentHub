from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from app.db.migrations.mission_control_plane import (
    CONTRACT_REVISION_BINDING_UPGRADE,
    MISSION_CONTROL_PLANE_UPGRADE,
    MISSION_EVENT_LEDGER_UPGRADE,
)
from app.domain import ActorRef, MissionContract
from app.repositories import MissionRepository
from app.services.mission_service import (
    ContractRevisionConflictError,
    MissionService,
)
from tests.domain.factories import build_contract, build_mission

_POSTGRES_DSN = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")
_MIGRATIONS = (
    MISSION_CONTROL_PLANE_UPGRADE
    + MISSION_EVENT_LEDGER_UPGRADE
    + CONTRACT_REVISION_BINDING_UPGRADE
)


class _BarrierContractRepository(MissionRepository):
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

    async def lock_contract_lineage(self, contract_id: str) -> None:
        await asyncio.wait_for(self._barrier.wait(), timeout=5)
        await super().lock_contract_lineage(contract_id)


@unittest.skipUnless(
    _POSTGRES_DSN,
    "AGENTHUB_TEST_POSTGRES_DSN is required for Contract revision lock tests",
)
class ContractRevisionPostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        assert _POSTGRES_DSN is not None
        self._schema = f"agenthub_contract_test_{uuid.uuid4().hex}"
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

        self._repository = _BarrierContractRepository(
            execute=execute,
            fetch_one=fetch_one,
            fetch_all=fetch_all,
            transaction_factory=transaction_factory,
            barrier=asyncio.Barrier(2),
        )
        initial = build_contract(version=1)
        await self._repository.add_contract(initial)
        await self._repository.add_mission(
            build_mission(
                id="mission-contract-revision",
                contract_id=initial.id,
                contract_version=initial.version,
            )
        )

    async def _drop_schema(self) -> None:
        assert _POSTGRES_DSN is not None
        connection = await asyncpg.connect(_POSTGRES_DSN)
        try:
            await connection.execute(
                f'DROP SCHEMA IF EXISTS "{self._schema}" CASCADE'
            )
        finally:
            await connection.close()

    async def test_concurrent_revision_writers_cannot_replace_same_version(
        self,
    ) -> None:
        service = MissionService(self._repository)
        candidates = (
            build_contract(
                version=2,
                governance={"decisionTimeoutSeconds": 900},
            ),
            build_contract(
                version=2,
                governance={"decisionTimeoutSeconds": 1800},
            ),
        )

        results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    service.revise_contract(
                        "mission-contract-revision",
                        expected_version=1,
                        contract=contract,
                        reason=f"Concurrent candidate {index}.",
                        actor=ActorRef(type="human", id=f"user-{index}"),
                    )
                    for index, contract in enumerate(candidates, start=1)
                ),
                return_exceptions=True,
            ),
            timeout=10,
        )

        successes = [result for result in results if isinstance(result, MissionContract)]
        conflicts = [
            result
            for result in results
            if isinstance(result, ContractRevisionConflictError)
        ]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].current_version, 2)

        async with self._pool.acquire() as connection:
            versions = await connection.fetch(
                """SELECT version FROM mission_contracts
                   WHERE id=$1 ORDER BY version""",
                "contract-1",
            )
            mission_version = await connection.fetchval(
                "SELECT contract_version FROM missions WHERE id=$1",
                "mission-contract-revision",
            )
            revision_events = await connection.fetchval(
                """SELECT count(*) FROM mission_events
                   WHERE aggregate_type='mission_contract'
                     AND aggregate_id=$1
                     AND event_type='contract.lifecycle.revised'""",
                "contract-1",
            )

        self.assertEqual([row["version"] for row in versions], [1, 2])
        self.assertEqual(mission_version, 1)
        self.assertEqual(revision_events, 1)
