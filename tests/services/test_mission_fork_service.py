from __future__ import annotations

import unittest
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy

from app.domain import (
    ActorRef,
    Artifact,
    ArtifactRef,
    EventEnvelope,
    ExecutionCheckpoint,
    Mission,
    MissionContract,
    WorkUnit,
)
from app.services.agent_binding_service import (
    AgentBinding,
    StaticAgentBindingResolver,
)
from app.services.artifact_integrity_service import (
    ArtifactByteVerification,
    ArtifactByteVerificationError,
)
from app.services.mission_service import MissionService, WorkUnitNotReadyError
from app.services.verification_evaluator_service import VerificationEvaluationError
from tests.domain.factories import (
    DIGEST,
    build_artifact,
    build_contract,
    build_execution_checkpoint,
    build_mission,
    build_work_unit,
)


class _ForkRepository:
    def __init__(self) -> None:
        source = build_mission(status="SUCCEEDED")
        contract = build_contract()
        work_unit = build_work_unit(status="SUCCEEDED", attempt=1)
        checkpoint = build_execution_checkpoint(
            sequence=5,
            phase="harness.execution.completed",
            terminal=True,
        )
        artifact = build_artifact()
        self.missions: dict[str, Mission] = {source.id: source}
        self.contracts: dict[tuple[str, int], MissionContract] = {
            (contract.id, contract.version): contract
        }
        self.contract_lineages = {contract.id: source.workspace_id}
        self.work_units: dict[str, WorkUnit] = {work_unit.id: work_unit}
        self.checkpoints: dict[str, ExecutionCheckpoint] = {checkpoint.id: checkpoint}
        self.artifacts: dict[str, Artifact] = {artifact.id: artifact}
        self.events: list[EventEnvelope] = []
        self.fail_add_work_unit = False

    @asynccontextmanager
    async def transaction(self):
        snapshot = deepcopy((self.missions, self.work_units, self.events))
        try:
            yield self
        except Exception:
            self.missions, self.work_units, self.events = snapshot
            raise

    async def get_mission(self, mission_id: str) -> Mission | None:
        return self.missions.get(mission_id)

    async def get_mission_for_update(self, mission_id: str) -> Mission | None:
        return self.missions.get(mission_id)

    async def add_mission(self, mission: Mission) -> None:
        if mission.id in self.missions:
            raise ValueError("Mission already exists")
        self.missions[mission.id] = mission

    async def get_contract(
        self,
        contract_id: str,
        contract_version: int,
    ) -> MissionContract | None:
        return self.contracts.get((contract_id, contract_version))

    async def get_contract_lineage_workspace(self, contract_id: str) -> str | None:
        return self.contract_lineages.get(contract_id)

    async def get_work_unit(self, work_unit_id: str) -> WorkUnit | None:
        return self.work_units.get(work_unit_id)

    async def get_work_unit_for_update(self, work_unit_id: str) -> WorkUnit | None:
        return self.work_units.get(work_unit_id)

    async def add_work_unit(self, work_unit: WorkUnit) -> None:
        if self.fail_add_work_unit:
            raise RuntimeError("simulated WorkUnit insert failure")
        if work_unit.id in self.work_units:
            raise ValueError("WorkUnit already exists")
        self.work_units[work_unit.id] = work_unit

    async def get_execution_checkpoint(
        self,
        checkpoint_id: str,
    ) -> ExecutionCheckpoint | None:
        return self.checkpoints.get(checkpoint_id)

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        return self.artifacts.get(artifact_id)

    async def append_event(self, event: EventEnvelope) -> None:
        self.events.append(event)


class _ArtifactVerifier:
    def __init__(
        self,
        *,
        results: Sequence[ArtifactByteVerification] | None = None,
        error: ArtifactByteVerificationError | None = None,
        on_verify: Callable[[], None] | None = None,
    ) -> None:
        self.results = results
        self.error = error
        self.on_verify = on_verify
        self.calls: list[tuple[Artifact, ...]] = []

    async def verify_all(
        self,
        artifacts: Sequence[Artifact],
    ) -> list[ArtifactByteVerification]:
        self.calls.append(tuple(artifacts))
        if self.error is not None:
            raise self.error
        if self.on_verify is not None:
            self.on_verify()
        if self.results is not None:
            return list(self.results)
        return [
            ArtifactByteVerification(
                artifact_id=artifact.id,
                digest=artifact.digest,
                size_bytes=artifact.size_bytes,
            )
            for artifact in artifacts
        ]


class MissionForkServiceTests(unittest.IsolatedAsyncioTestCase):
    def _service(
        self,
        repository: _ForkRepository,
        verifier: _ArtifactVerifier,
    ) -> MissionService:
        resolver = StaticAgentBindingResolver(
            {
                ("workspace-1", "reviewer"): AgentBinding(
                    agent_id="reviewer",
                    adapter_type="local_codex",
                    capabilities=("repository.write",),
                )
            }
        )
        return MissionService(
            repository,  # type: ignore[arg-type]
            artifact_byte_verifier=verifier,
            agent_binding_resolver=resolver,
        )

    async def _fork(
        self,
        repository: _ForkRepository,
        verifier: _ArtifactVerifier,
        **updates: object,
    ):
        values = {
            "mission_id": "mis-fork",
            "work_unit_id": "wu-fork",
            "title": "Continue from verified output",
            "objective": "Use the verified diff as the next bounded input.",
            "checkpoint_id": "chk-1",
            "artifact_refs": [ArtifactRef(id="artifact-1", digest=DIGEST)],
            "expected_outputs": [],
            "required_capabilities": ["repository.write"],
            "agent_id": "reviewer",
            "actor": ActorRef(type="human", id="user-1"),
        }
        values.update(updates)
        return await self._service(repository, verifier).fork_mission(
            "mis-1",
            **values,  # type: ignore[arg-type]
        )

    async def test_creates_new_mission_and_root_then_replays_idempotently(self) -> None:
        repository = _ForkRepository()
        verifier = _ArtifactVerifier()

        outcome = await self._fork(repository, verifier)

        self.assertEqual(outcome.mission.status.value, "READY")
        self.assertEqual(outcome.mission.contract_id, "contract-1")
        self.assertEqual(outcome.mission.contract_version, 1)
        self.assertEqual(outcome.mission.source.type.value, "mission.fork")
        self.assertEqual(outcome.mission.source.reference, "mis-1")
        self.assertEqual(outcome.mission.source.external_id, "chk-1")
        self.assertEqual(outcome.work_unit.status.value, "PENDING")
        self.assertEqual(outcome.work_unit.attempt, 0)
        self.assertEqual(outcome.work_unit.input_refs[0].id, "artifact-1")
        self.assertEqual(outcome.work_unit.assigned_agent_id, "reviewer")
        self.assertEqual(outcome.work_unit.assigned_adapter, "local_codex")
        self.assertEqual(len(repository.events), 2)
        self.assertEqual(
            repository.events[1].causation_id,
            repository.events[0].event_id,
        )

        replay = await self._fork(repository, verifier)

        self.assertEqual(replay, outcome)
        self.assertEqual(len(repository.events), 2)
        self.assertEqual(len(verifier.calls), 1)
        with self.assertRaisesRegex(ValueError, "different content"):
            await self._fork(repository, verifier, objective="Conflicting objective")
        self.assertEqual(len(repository.events), 2)

    async def test_rejects_non_successful_checkpoint_or_source_attempt(self) -> None:
        cases = (
            build_execution_checkpoint(),
            build_execution_checkpoint(
                phase="harness.execution.failed",
                terminal=True,
                failure_reason="execution failed",
            ),
        )
        for checkpoint in cases:
            with self.subTest(phase=checkpoint.phase.value):
                repository = _ForkRepository()
                repository.checkpoints["chk-1"] = checkpoint
                verifier = _ArtifactVerifier()
                with self.assertRaisesRegex(
                    WorkUnitNotReadyError,
                    "successful terminal checkpoint",
                ):
                    await self._fork(repository, verifier)
                self.assertNotIn("mis-fork", repository.missions)
                self.assertEqual(verifier.calls, [])

        repository = _ForkRepository()
        repository.work_units["wu-1"] = build_work_unit(
            status="SUCCEEDED",
            attempt=2,
        )
        with self.assertRaisesRegex(WorkUnitNotReadyError, "does not match"):
            await self._fork(repository, _ArtifactVerifier())
        self.assertNotIn("mis-fork", repository.missions)

    async def test_rejects_artifact_or_byte_result_outside_exact_attempt(self) -> None:
        repository = _ForkRepository()
        repository.artifacts["artifact-1"] = build_artifact(attempt=2)
        verifier = _ArtifactVerifier()
        with self.assertRaisesRegex(WorkUnitNotReadyError, "another attempt"):
            await self._fork(repository, verifier)
        self.assertEqual(verifier.calls, [])

        repository = _ForkRepository()
        verifier = _ArtifactVerifier(results=[])
        with self.assertRaisesRegex(VerificationEvaluationError, "exactly match"):
            await self._fork(repository, verifier)
        self.assertNotIn("mis-fork", repository.missions)
        self.assertEqual(repository.events, [])

    async def test_rejects_artifact_drift_after_byte_verification(self) -> None:
        repository = _ForkRepository()

        def replace_artifact() -> None:
            repository.artifacts["artifact-1"] = build_artifact(size_bytes=129)

        verifier = _ArtifactVerifier(on_verify=replace_artifact)
        with self.assertRaisesRegex(WorkUnitNotReadyError, "source changed"):
            await self._fork(repository, verifier)

        self.assertNotIn("mis-fork", repository.missions)
        self.assertNotIn("wu-fork", repository.work_units)
        self.assertEqual(repository.events, [])

    async def test_transaction_rolls_back_partial_creation(self) -> None:
        repository = _ForkRepository()
        repository.fail_add_work_unit = True

        with self.assertRaisesRegex(RuntimeError, "insert failure"):
            await self._fork(repository, _ArtifactVerifier())

        self.assertNotIn("mis-fork", repository.missions)
        self.assertNotIn("wu-fork", repository.work_units)
        self.assertEqual(repository.events, [])


if __name__ == "__main__":
    unittest.main()
