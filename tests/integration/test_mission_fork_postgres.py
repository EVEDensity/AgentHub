from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from app.db.migrations.mission_control_plane import (
    AGENT_BINDING_PERSISTENCE_UPGRADE,
    ARTIFACT_PERSISTENCE_UPGRADE,
    ARTIFACT_TABLE_OWNERSHIP_UPGRADE,
    CONTRACT_LINEAGE_OWNERSHIP_UPGRADE,
    CONTRACT_REVISION_BINDING_UPGRADE,
    DELEGATION_PERSISTENCE_UPGRADE,
    EXECUTION_CHECKPOINT_UPGRADE,
    MISSION_CONTROL_PLANE_UPGRADE,
    MISSION_EVENT_LEDGER_UPGRADE,
    WORK_UNIT_PERSISTENCE_UPGRADE,
)
from app.domain import ActorRef, Artifact, ArtifactRef
from app.repositories import MissionRepository
from app.services.agent_binding_service import (
    AgentBinding,
    StaticAgentBindingResolver,
)
from app.services.artifact_integrity_service import ArtifactByteVerification
from app.services.mission_service import MissionForkOutcome, MissionService
from app.services.workspace_admission_service import WorkspaceClaimAdmissionPolicy
from tests.domain.factories import (
    DIGEST,
    build_artifact,
    build_contract,
    build_execution_checkpoint,
    build_mission,
    build_work_unit,
)

_POSTGRES_DSN = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")
_MIGRATIONS = (
    MISSION_CONTROL_PLANE_UPGRADE
    + MISSION_EVENT_LEDGER_UPGRADE
    + WORK_UNIT_PERSISTENCE_UPGRADE
    + ARTIFACT_PERSISTENCE_UPGRADE
    + DELEGATION_PERSISTENCE_UPGRADE
    + AGENT_BINDING_PERSISTENCE_UPGRADE
    + ARTIFACT_TABLE_OWNERSHIP_UPGRADE
    + CONTRACT_REVISION_BINDING_UPGRADE
    + CONTRACT_LINEAGE_OWNERSHIP_UPGRADE
    + EXECUTION_CHECKPOINT_UPGRADE
)


class _BarrierForkRepository(MissionRepository):
    """Release concurrent writers together immediately before the source lock."""

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

    async def get_mission_for_update(self, mission_id: str):
        if mission_id == "mis-1":
            await asyncio.wait_for(self._barrier.wait(), timeout=5)
        return await super().get_mission_for_update(mission_id)


class _ArtifactVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[Artifact, ...]] = []

    async def verify_all(
        self,
        artifacts: Sequence[Artifact],
    ) -> list[ArtifactByteVerification]:
        self.calls.append(tuple(artifacts))
        return [
            ArtifactByteVerification(
                artifact_id=artifact.id,
                digest=artifact.digest,
                size_bytes=artifact.size_bytes,
            )
            for artifact in artifacts
        ]


@unittest.skipUnless(
    _POSTGRES_DSN,
    "AGENTHUB_TEST_POSTGRES_DSN is required for Mission fork lock tests",
)
class MissionForkPostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        assert _POSTGRES_DSN is not None
        self._schema = f"agenthub_fork_test_{uuid.uuid4().hex}"
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

        self._repository = _BarrierForkRepository(
            execute=execute,
            fetch_one=fetch_one,
            fetch_all=fetch_all,
            transaction_factory=transaction_factory,
            barrier=asyncio.Barrier(2),
        )
        await self._repository.add_contract_lineage("contract-1", "workspace-1")
        await self._repository.add_contract(build_contract())
        await self._repository.add_mission(build_mission(status="SUCCEEDED"))
        await self._repository.add_work_unit(
            build_work_unit(status="SUCCEEDED", attempt=1)
        )
        await self._repository.add_execution_checkpoint(
            build_execution_checkpoint(
                sequence=5,
                phase="harness.execution.completed",
                terminal=True,
            )
        )
        await self._repository.add_artifact(build_artifact())

        self._verifier = _ArtifactVerifier()
        resolver = StaticAgentBindingResolver(
            {
                ("workspace-1", "reviewer"): AgentBinding(
                    agent_id="reviewer",
                    adapter_type="local_codex",
                    capabilities=("repository.write",),
                )
            }
        )
        self._service = MissionService(
            self._repository,
            artifact_byte_verifier=self._verifier,
            agent_binding_resolver=resolver,
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

    async def _fork(self, *, objective: str) -> MissionForkOutcome:
        return await self._service.fork_mission(
            "mis-1",
            mission_id="mis-fork",
            work_unit_id="wu-fork",
            title="Continue from verified output",
            objective=objective,
            checkpoint_id="chk-1",
            artifact_refs=[ArtifactRef(id="artifact-1", digest=DIGEST)],
            expected_outputs=[],
            required_capabilities=["repository.write"],
            agent_id="reviewer",
            actor=ActorRef(type="human", id="user-1"),
        )

    async def _fork_counts(self) -> tuple[int, int, int, int, int]:
        async with self._pool.acquire() as connection:
            return (
                await connection.fetchval(
                    "SELECT count(*) FROM missions WHERE id='mis-fork'"
                ),
                await connection.fetchval(
                    "SELECT count(*) FROM work_units WHERE id='wu-fork'"
                ),
                await connection.fetchval(
                    "SELECT count(*) FROM mission_events WHERE correlation_id='mis-fork'"
                ),
                await connection.fetchval("SELECT count(*) FROM mission_artifacts"),
                await connection.fetchval("SELECT count(*) FROM execution_checkpoints"),
            )

    async def test_concurrent_identical_forks_converge_without_duplicate_state(
        self,
    ) -> None:
        objective = "Use verified artifacts as bounded input."

        results = await asyncio.wait_for(
            asyncio.gather(
                self._fork(objective=objective),
                self._fork(objective=objective),
            ),
            timeout=10,
        )

        self.assertEqual(results[0], results[1])
        self.assertEqual(await self._fork_counts(), (1, 1, 2, 1, 1))
        self.assertEqual(len(self._verifier.calls), 2)

        replay = await self._fork(objective=objective)
        self.assertEqual(replay, results[0])
        self.assertEqual(len(self._verifier.calls), 2)

        await self._service.start_mission(
            "mis-fork",
            actor=ActorRef(type="human", id="user-1"),
        )
        claim = await self._service.claim_workspace_bound_work_unit(
            "workspace-1",
            agent_id="reviewer",
            adapter_type="local_codex",
            supported_work_unit_kinds=("mission.fork",),
            runner_id="runner-1",
            actor=ActorRef(type="runner", id="runner-1"),
            lease_seconds=60,
            admission_policy=WorkspaceClaimAdmissionPolicy(
                tenant_id="tenant-test",
                max_concurrent=0,
            ),
        )
        self.assertEqual(claim.status.value, "claimed")
        self.assertIsNotNone(claim.work_unit)
        assert claim.work_unit is not None
        self.assertEqual(claim.work_unit.id, "wu-fork")
        self.assertEqual(claim.work_unit.status.value, "LEASED")
        self.assertEqual(claim.work_unit.attempt, 1)
        self.assertEqual(claim.work_unit.input_refs[0].id, "artifact-1")
        self.assertIsNotNone(claim.work_unit.lease)
        assert claim.work_unit.lease is not None
        self.assertEqual(await self._fork_counts(), (1, 1, 4, 1, 1))

        context = await self._service.get_claimed_execution_context(
            "mis-fork",
            "wu-fork",
            lease_id=claim.work_unit.lease.id,
            runner_id="runner-1",
        )
        self.assertEqual(context.mission.source.type.value, "mission.fork")
        self.assertEqual(context.mission.source.reference, "mis-1")
        self.assertEqual(context.mission.source.external_id, "chk-1")
        self.assertEqual(context.work_unit.input_refs[0].id, "artifact-1")
        self.assertEqual(context.work_unit.attempt, 1)

        source_work_unit = await self._repository.get_work_unit("wu-1")
        self.assertIsNotNone(source_work_unit)
        assert source_work_unit is not None
        self.assertIsNone(source_work_unit.lease)

    async def test_concurrent_conflicting_forks_cannot_overwrite_winner(self) -> None:
        objectives = ("Continue with path A.", "Continue with path B.")

        results = await asyncio.wait_for(
            asyncio.gather(
                *(self._fork(objective=objective) for objective in objectives),
                return_exceptions=True,
            ),
            timeout=10,
        )

        successes = [result for result in results if isinstance(result, MissionForkOutcome)]
        conflicts = [result for result in results if isinstance(result, ValueError)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("different content", str(conflicts[0]))
        self.assertEqual(await self._fork_counts(), (1, 1, 2, 1, 1))

        stored = await self._repository.get_mission("mis-fork")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertIn(stored.objective, objectives)
