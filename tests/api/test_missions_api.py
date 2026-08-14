from __future__ import annotations

import unittest
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.missions import (
    get_agent_binding_resolver,
    get_artifact_byte_verifier,
    get_mission_repository,
    router,
)
from app.domain import (
    Artifact,
    EventEnvelope,
    Evidence,
    Lease,
    Mission,
    MissionContract,
    WorkUnit,
)
from app.services.agent_binding_service import (
    AgentBinding,
    AgentBindingResolver,
    DatabaseAgentBindingResolver,
    StaticAgentBindingResolver,
    UnavailableAgentBindingResolver,
)
from app.services.artifact_integrity_service import (
    ArtifactBytesUnavailableError,
    ArtifactByteVerification,
    ArtifactByteVerificationError,
    ArtifactIntegrityError,
)
from app.services.auth_service import get_current_user
from tests.domain.factories import (
    build_artifact,
    build_contract,
    build_event,
    build_evidence,
    build_mission,
    build_work_unit,
)


class FakeMissionRepository:
    def __init__(self) -> None:
        self.contract: MissionContract | None = None
        self.mission: Mission | None = None
        self.events: list[EventEnvelope] = []
        self.artifacts: list[Artifact] = []
        self.evidence: list[Evidence] = []
        self.list_result: list[Mission] = []
        self.work_units: list[WorkUnit] = []
        self.transaction_depth = 0

    @asynccontextmanager
    async def transaction(self):
        self.transaction_depth += 1
        try:
            yield self
        finally:
            self.transaction_depth -= 1

    async def add_contract(self, contract: MissionContract) -> None:
        self.contract = contract

    async def get_contract(self, contract_id: str) -> MissionContract | None:
        if self.contract and self.contract.id == contract_id:
            return self.contract
        return None

    async def add_mission(self, mission: Mission) -> None:
        self.mission = mission

    async def get_mission(self, mission_id: str) -> Mission | None:
        if self.mission and self.mission.id == mission_id:
            return self.mission
        return None

    async def get_mission_by_source(
        self,
        workspace_id: str,
        *,
        source_type: str,
        external_id: str,
    ) -> Mission | None:
        mission = self.mission
        if (
            mission is not None
            and mission.workspace_id == workspace_id
            and mission.source.type.value == source_type
            and mission.source.external_id == external_id
        ):
            return mission
        return None

    async def get_mission_for_update(self, mission_id: str) -> Mission | None:
        return await self.get_mission(mission_id)

    async def update_mission(self, mission: Mission) -> None:
        self.mission = mission

    async def get_last_event_sequence(
        self,
        aggregate_id: str,
        *,
        aggregate_type: str = "mission",
    ) -> int:
        sequences = [
            event.sequence
            for event in self.events
            if event.aggregate_type.value == aggregate_type
            and event.aggregate_id == aggregate_id
        ]
        return max(sequences, default=0)

    async def list_events(
        self,
        mission_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[EventEnvelope]:
        events = sorted(
            (
                event
                for event in self.events
                if event.aggregate_type.value == "mission"
                and event.aggregate_id == mission_id
                and event.sequence > after_sequence
            ),
            key=lambda event: event.sequence,
        )
        return events[:limit]

    async def add_evidence(self, evidence: Evidence) -> None:
        self.evidence.append(evidence)

    async def get_evidence(self, evidence_id: str) -> Evidence | None:
        return next(
            (evidence for evidence in self.evidence if evidence.id == evidence_id),
            None,
        )

    async def list_evidence(
        self,
        mission_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Evidence]:
        matching = sorted(
            (
                evidence
                for evidence in self.evidence
                if evidence.mission_id == mission_id
            ),
            key=lambda evidence: (evidence.generated_at, evidence.id),
        )
        return matching[offset : offset + limit]

    async def list_passed_evidence_criterion_ids(self, mission_id: str) -> set[str]:
        return {
            evidence.criterion_id
            for evidence in self.evidence
            if evidence.mission_id == mission_id and evidence.verdict.value == "PASS"
        }

    async def add_artifact(self, artifact: Artifact) -> None:
        self.artifacts.append(artifact)

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        return next(
            (artifact for artifact in self.artifacts if artifact.id == artifact_id),
            None,
        )

    async def list_artifacts(
        self,
        mission_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]:
        matching = sorted(
            (
                artifact
                for artifact in self.artifacts
                if artifact.mission_id == mission_id
            ),
            key=lambda artifact: (artifact.created_at, artifact.id),
        )
        return matching[offset : offset + limit]

    async def list_missions(
        self,
        workspace_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Mission]:
        return [
            mission
            for mission in self.list_result
            if mission.workspace_id == workspace_id
        ][offset : offset + limit]

    async def add_work_unit(self, work_unit: WorkUnit) -> None:
        self.work_units.append(work_unit)

    async def get_work_unit(self, work_unit_id: str) -> WorkUnit | None:
        return next(
            (
                work_unit
                for work_unit in self.work_units
                if work_unit.id == work_unit_id
            ),
            None,
        )

    async def get_work_unit_for_update(self, work_unit_id: str) -> WorkUnit | None:
        return await self.get_work_unit(work_unit_id)

    async def get_delegated_work_unit_for_claim(
        self,
        mission_id: str,
        *,
        agent_id: str,
        adapter_type: str,
    ) -> WorkUnit | None:
        candidates = sorted(
            (
                work_unit
                for work_unit in self.work_units
                if work_unit.mission_id == mission_id
                and work_unit.parent_work_unit_id is not None
                and work_unit.assigned_agent_id == agent_id
                and work_unit.assigned_adapter == adapter_type
                and work_unit.status.value in {"PENDING", "RETRYING"}
                and all(
                    (
                        dependency := next(
                            (
                                item
                                for item in self.work_units
                                if item.id == dependency_id
                            ),
                            None,
                        )
                    )
                    is not None
                    and dependency.mission_id == mission_id
                    and dependency.status.value == "SUCCEEDED"
                    for dependency_id in work_unit.dependencies
                )
            ),
            key=lambda work_unit: work_unit.id,
        )
        return candidates[0] if candidates else None

    async def update_work_unit(self, work_unit: WorkUnit) -> None:
        for index, existing in enumerate(self.work_units):
            if existing.id == work_unit.id:
                self.work_units[index] = work_unit
                return
        self.work_units.append(work_unit)

    async def list_work_units(
        self,
        mission_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkUnit]:
        matching = [
            work_unit
            for work_unit in self.work_units
            if work_unit.mission_id == mission_id
        ]
        return sorted(matching, key=lambda work_unit: work_unit.id)[
            offset : offset + limit
        ]

    async def list_work_units_for_update(self, mission_id: str) -> list[WorkUnit]:
        return sorted(
            (
                work_unit
                for work_unit in self.work_units
                if work_unit.mission_id == mission_id
            ),
            key=lambda work_unit: work_unit.id,
        )

    async def append_event(self, event: EventEnvelope) -> None:
        self.events.append(event)


class FakeArtifactByteVerifier:
    def __init__(
        self,
        *,
        error: ArtifactByteVerificationError | None = None,
        on_verify: Callable[[list[Artifact]], None] | None = None,
    ) -> None:
        self.error = error
        self.on_verify = on_verify
        self.calls: list[list[Artifact]] = []
        self.repository: FakeMissionRepository | None = None

    async def verify_all(
        self,
        artifacts: list[Artifact],
    ) -> list[ArtifactByteVerification]:
        if self.repository is not None and self.repository.transaction_depth:
            raise AssertionError("artifact storage I/O ran inside a transaction")
        self.calls.append(list(artifacts))
        if self.on_verify is not None:
            self.on_verify(artifacts)
        if self.error is not None:
            raise self.error
        return [
            ArtifactByteVerification(
                artifact_id=artifact.id,
                digest=artifact.digest,
                size_bytes=artifact.size_bytes,
            )
            for artifact in artifacts
        ]


def build_app(
    repository: FakeMissionRepository,
    user: dict[str, Any],
    *,
    artifact_byte_verifier: FakeArtifactByteVerifier | None = None,
    agent_binding_resolver: AgentBindingResolver | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    verifier = artifact_byte_verifier or FakeArtifactByteVerifier()
    verifier.repository = repository
    app.dependency_overrides[get_mission_repository] = lambda: repository
    app.dependency_overrides[get_artifact_byte_verifier] = lambda: verifier
    binding_resolver = agent_binding_resolver or UnavailableAgentBindingResolver()
    app.dependency_overrides[get_agent_binding_resolver] = lambda: binding_resolver
    app.dependency_overrides[get_current_user] = lambda: user
    return app


class MissionApiTests(unittest.TestCase):
    def test_default_agent_binding_dependency_uses_durable_catalog(self) -> None:
        self.assertIsInstance(
            get_agent_binding_resolver(),
            DatabaseAgentBindingResolver,
        )

    def test_create_mission_derives_actor_and_appends_first_event(self) -> None:
        repository = FakeMissionRepository()
        user = {"id": "user-1", "name": "Ada", "role": "developer"}
        client = TestClient(build_app(repository, user))

        response = client.post(
            "/api/v1/missions",
            json={
                "id": "mis-api-1",
                "workspaceId": "user-1",
                "title": "Ship Mission API",
                "objective": "Create the first Mission endpoint.",
                "source": {"type": "api", "reference": "local-test"},
                "contract": build_contract().to_public_dict(),
                "createdBy": {"type": "human", "id": "forged"},
            },
        )

        self.assertEqual(response.status_code, 422)

        valid_response = client.post(
            "/api/v1/missions",
            json={
                "id": "mis-api-1",
                "workspaceId": "user-1",
                "title": "Ship Mission API",
                "objective": "Create the first Mission endpoint.",
                "source": {"type": "api", "reference": "local-test"},
                "contract": build_contract().to_public_dict(),
            },
        )

        self.assertEqual(valid_response.status_code, 201)
        body = valid_response.json()
        self.assertEqual(
            body["createdBy"], {"type": "human", "id": "user-1", "displayName": "Ada"}
        )
        self.assertEqual(body["status"], "READY")
        self.assertIsNotNone(repository.mission)
        self.assertEqual(len(repository.events), 1)
        event = repository.events[0]
        self.assertEqual(event.sequence, 1)
        self.assertEqual(event.event_type, "mission.lifecycle.created")
        self.assertEqual(event.actor.id, "user-1")

    def test_non_admin_cannot_access_another_workspace(self) -> None:
        repository = FakeMissionRepository()
        user = {"id": "user-1", "name": "Ada", "role": "developer"}
        client = TestClient(build_app(repository, user))

        response = client.get("/api/v1/missions?workspaceId=workspace-2")

        self.assertEqual(response.status_code, 403)

    def test_get_and_list_return_public_contracts(self) -> None:
        repository = FakeMissionRepository()
        mission = build_mission(workspace_id="workspace-1")
        repository.mission = mission
        repository.list_result = [mission]
        user = {"id": "admin-1", "name": "Root", "role": "admin"}
        client = TestClient(build_app(repository, user))

        get_response = client.get(f"/api/v1/missions/{mission.id}")
        list_response = client.get("/api/v1/missions?workspaceId=workspace-1")

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()["workspaceId"], "workspace-1")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["missions"][0]["id"], mission.id)

    def test_start_updates_snapshot_and_appends_actor_event(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1")
        repository.events = [build_event()]
        user = {"id": "user-1", "name": "Ada", "role": "developer"}
        client = TestClient(build_app(repository, user))

        response = client.post("/api/v1/missions/mis-1/start")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "RUNNING")
        self.assertIsNotNone(repository.mission)
        self.assertEqual(repository.mission.status.value, "RUNNING")
        self.assertEqual(len(repository.events), 2)
        event = repository.events[-1]
        self.assertEqual(event.sequence, 2)
        self.assertEqual(event.event_type, "mission.lifecycle.started")
        self.assertEqual(event.actor.id, "user-1")
        self.assertEqual(
            event.payload,
            {"previousStatus": "READY", "status": "RUNNING"},
        )

    def test_cancel_ready_mission(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1")
        repository.events = [build_event()]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "CANCELLED")
        self.assertEqual(
            repository.events[-1].event_type, "mission.lifecycle.cancelled"
        )

    def test_cancel_mission_cancels_non_terminal_work_units(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.events = [build_event()]
        repository.work_units = [
            build_work_unit(id="wu-pending"),
            build_work_unit(
                id="wu-running",
                status="RUNNING",
                lease=Lease(
                    id="lease-running",
                    runner_id="runner-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            ),
            build_work_unit(id="wu-succeeded", status="SUCCEEDED"),
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "CANCELLED")
        by_id = {work_unit.id: work_unit for work_unit in repository.work_units}
        self.assertEqual(by_id["wu-pending"].status.value, "CANCELLED")
        self.assertEqual(by_id["wu-running"].status.value, "CANCELLED")
        self.assertIsNone(by_id["wu-running"].lease)
        self.assertEqual(by_id["wu-succeeded"].status.value, "SUCCEEDED")
        cancellation_events = repository.events[1:]
        self.assertEqual(
            [event.event_type for event in cancellation_events],
            [
                "mission.lifecycle.cancelled",
                "work_unit.lifecycle.cancelled",
                "work_unit.lifecycle.cancelled",
            ],
        )
        self.assertTrue(
            all(
                event.causation_id == cancellation_events[0].event_id
                for event in cancellation_events[1:]
            )
        )

    def test_invalid_transition_returns_conflict_without_event(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
        )
        repository.events = [build_event()]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/start")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(repository.events), 1)
        self.assertEqual(repository.mission.status.value, "RUNNING")

    def test_lifecycle_command_returns_not_found(self) -> None:
        client = TestClient(
            build_app(
                FakeMissionRepository(),
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/missing/start")

        self.assertEqual(response.status_code, 404)

    def test_events_are_ordered_filtered_and_workspace_scoped(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="workspace-1")
        repository.events = [
            build_event(
                sequence=2, event_id="evt-2", event_type="mission.lifecycle.started"
            ),
            build_event(),
        ]
        admin_client = TestClient(
            build_app(
                repository,
                {"id": "admin-1", "name": "Root", "role": "admin"},
            )
        )

        response = admin_client.get("/api/v1/missions/mis-1/events?afterSequence=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [event["sequence"] for event in response.json()["events"]],
            [2],
        )

        other_user_client = TestClient(
            build_app(
                repository,
                {"id": "user-2", "name": "Grace", "role": "developer"},
            )
        )
        denied = other_user_client.get("/api/v1/missions/mis-1/events")
        self.assertEqual(denied.status_code, 403)

    def test_create_and_list_pending_work_unit_with_event(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units",
            json={
                "id": "wu-api-1",
                "kind": "code_change",
                "expectedOutputs": [{"kind": "diff", "required": True}],
                "requiredCapabilities": ["repository.write"],
                "assignedAdapter": "codex",
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "PENDING")
        self.assertEqual(response.json()["attempt"], 0)
        self.assertEqual(len(repository.work_units), 1)
        event = repository.events[-1]
        self.assertEqual(event.aggregate_type.value, "work_unit")
        self.assertEqual(event.aggregate_id, "wu-api-1")
        self.assertEqual(event.event_type, "work_unit.lifecycle.created")
        self.assertEqual(event.actor.id, "user-1")
        self.assertEqual(event.correlation_id, "mis-1")

        listed = client.get("/api/v1/missions/mis-1/work-units")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["workUnits"][0]["id"], "wu-api-1")

    def test_work_unit_requires_running_mission_and_contract_capability(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1")
        repository.contract = build_contract()
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )
        request = {
            "kind": "code_change",
            "requiredCapabilities": ["production.deploy"],
        }

        not_running = client.post(
            "/api/v1/missions/mis-1/work-units",
            json=request,
        )
        self.assertEqual(not_running.status_code, 409)

        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        unsupported = client.post(
            "/api/v1/missions/mis-1/work-units",
            json=request,
        )
        self.assertEqual(unsupported.status_code, 409)
        self.assertEqual(repository.work_units, [])
        self.assertEqual(repository.events, [])

    def test_delegate_work_unit_requires_active_lease_and_registered_artifact(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                id="wu-parent",
                status="RUNNING",
                attempt=2,
                lease=Lease(
                    id="lease-parent",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        repository.artifacts = [build_artifact(work_unit_id="wu-parent", attempt=2)]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
                agent_binding_resolver=StaticAgentBindingResolver(
                    {
                        ("user-1", "reviewer"): AgentBinding(
                            agent_id="reviewer",
                            adapter_type="local_codex",
                            capabilities=("repository.write",),
                        )
                    }
                ),
            )
        )
        request = {
            "id": "wu-child",
            "agentId": "reviewer",
            "leaseId": "lease-parent",
            "inputRefs": [
                {"id": "artifact-1", "digest": repository.artifacts[0].digest}
            ],
            "requiredCapabilities": ["repository.write"],
        }

        first = client.post(
            "/api/v1/missions/mis-1/work-units/wu-parent/delegations",
            json=request,
        )
        second = client.post(
            "/api/v1/missions/mis-1/work-units/wu-parent/delegations",
            json=request,
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json()["parentWorkUnitId"], "wu-parent")
        self.assertEqual(len(repository.work_units), 2)
        self.assertEqual(len(repository.events), 2)
        self.assertEqual(repository.events[0].event_type, "work_unit.delegation.requested")
        self.assertEqual(repository.events[1].event_type, "work_unit.lifecycle.created")
        self.assertEqual(
            repository.events[1].causation_id,
            repository.events[0].event_id,
        )

        repository.work_units[0] = build_work_unit(
            id="wu-parent",
            status="RUNNING",
            attempt=2,
            lease=Lease(
                id="lease-parent",
                runner_id="user-1",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ),
        )
        rejected = client.post(
            "/api/v1/missions/mis-1/work-units/wu-parent/delegations",
            json={**request, "id": "wu-child-2", "inputRefs": []},
        )
        self.assertEqual(rejected.status_code, 422)

        restricted_client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
                agent_binding_resolver=StaticAgentBindingResolver(
                    {
                        ("user-1", "reviewer"): AgentBinding(
                            agent_id="reviewer",
                            adapter_type="local_codex",
                            capabilities=("repository.read",),
                        )
                    }
                ),
            )
        )
        capability_gap = restricted_client.post(
            "/api/v1/missions/mis-1/work-units/wu-parent/delegations",
            json={**request, "id": "wu-child-3"},
        )
        self.assertEqual(capability_gap.status_code, 409)
        self.assertEqual(len(repository.work_units), 2)

    def test_delegate_work_unit_fails_closed_without_authorized_binding(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                id="wu-parent",
                status="RUNNING",
                lease=Lease(
                    id="lease-parent",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        repository.artifacts = [build_artifact(work_unit_id="wu-parent")]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-parent/delegations",
            json={
                "id": "wu-child",
                "agentId": "reviewer",
                "leaseId": "lease-parent",
                "inputRefs": [
                    {"id": "artifact-1", "digest": repository.artifacts[0].digest}
                ],
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(repository.work_units), 1)
        self.assertEqual(repository.events, [])

    def test_work_unit_rejects_dependency_outside_mission(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(id="wu-other", mission_id="mis-other")]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units",
            json={"kind": "code_change", "dependencies": ["wu-other"]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(repository.work_units), 1)
        self.assertEqual(repository.events, [])

    def test_lease_claim_updates_attempt_and_appends_event(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [build_work_unit()]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/lease",
            json={"leaseSeconds": 60},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "LEASED")
        self.assertEqual(body["attempt"], 1)
        self.assertEqual(body["lease"]["runnerId"], "user-1")
        self.assertEqual(len(repository.events), 1)
        event = repository.events[-1]
        self.assertEqual(event.sequence, 1)
        self.assertEqual(event.event_type, "work_unit.lifecycle.leased")
        self.assertEqual(event.actor.id, "user-1")
        self.assertEqual(event.payload["attempt"], 1)

    def test_lease_claim_requires_completed_dependencies(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(id="wu-dependency"),
            build_work_unit(id="wu-child", dependencies=["wu-dependency"]),
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/work-units/wu-child/lease")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[1].status.value, "PENDING")
        self.assertEqual(repository.events, [])

    def test_delegated_claim_selects_matching_ready_unit_atomically(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(id="wu-parent", status="SUCCEEDED"),
            build_work_unit(
                id="wu-child",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            ),
            build_work_unit(
                id="wu-other-agent",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="other",
                assigned_adapter="local_codex",
            ),
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={
                "agentId": "reviewer",
                "adapterType": "local_codex",
                "leaseSeconds": 60,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()["workUnit"]
        self.assertEqual(body["id"], "wu-child")
        self.assertEqual(body["status"], "LEASED")
        self.assertEqual(body["attempt"], 1)
        self.assertEqual(body["lease"]["runnerId"], "user-1")
        self.assertEqual(repository.events[-1].actor.type.value, "runner")
        self.assertEqual(repository.events[-1].payload["claimMode"], "delegated")

    def test_delegated_claim_returns_empty_when_binding_has_no_ready_unit(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                id="wu-child",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="reviewer",
                assigned_adapter="remote",
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={"agentId": "reviewer", "adapterType": "local_codex"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["workUnit"])
        self.assertEqual(repository.events, [])

    def test_start_requires_matching_active_lease(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="LEASED",
                lease=Lease(
                    id="lease-1",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/start",
            json={"leaseId": "wrong-lease"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].status.value, "LEASED")
        self.assertEqual(repository.events, [])

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/start",
            json={"leaseId": "lease-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "RUNNING")
        self.assertIsNotNone(repository.work_units[0].lease)
        self.assertEqual(
            repository.events[-1].event_type, "work_unit.lifecycle.started"
        )

    def test_heartbeat_renews_active_lease_and_appends_event(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        previous_expiry = datetime.now(timezone.utc) + timedelta(seconds=30)
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=previous_expiry,
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/heartbeat",
            json={"leaseId": "lease-running", "leaseSeconds": 300},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "RUNNING")
        self.assertEqual(body["lease"]["id"], "lease-running")
        renewed_expiry = datetime.fromisoformat(body["lease"]["expiresAt"])
        self.assertGreater(renewed_expiry, previous_expiry)
        self.assertEqual(
            repository.events[-1].event_type,
            "work_unit.lifecycle.heartbeat",
        )
        self.assertEqual(repository.events[-1].payload["leaseId"], "lease-running")

    def test_heartbeat_rejects_expired_or_mismatched_lease(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="LEASED",
                lease=Lease(
                    id="lease-expired",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/heartbeat",
            json={"leaseId": "wrong-lease"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].lease.id, "lease-expired")
        self.assertEqual(repository.events, [])

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/heartbeat",
            json={"leaseId": "lease-expired"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].lease.id, "lease-expired")
        self.assertEqual(repository.events, [])

    def test_expired_lease_is_recovered_to_retrying(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                status="LEASED",
                attempt=2,
                lease=Lease(
                    id="lease-expired",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/work-units/wu-1/recover")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "RETRYING")
        self.assertEqual(response.json()["attempt"], 2)
        self.assertIsNone(repository.work_units[0].lease)
        self.assertEqual(
            repository.events[-1].event_type,
            "work_unit.lifecycle.lease_expired",
        )
        self.assertFalse(repository.events[-1].payload["retryBudgetExhausted"])

    def test_expired_lease_fails_when_retry_budget_is_exhausted(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=3,
                lease=Lease(
                    id="lease-expired",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/work-units/wu-1/recover")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "FAILED")
        self.assertEqual(response.json()["attempt"], 3)
        self.assertIsNone(repository.work_units[0].lease)
        self.assertEqual(repository.mission.status.value, "FAILED")
        self.assertEqual(
            repository.events[-2].event_type,
            "work_unit.lifecycle.lease_expired",
        )
        self.assertTrue(repository.events[-2].payload["retryBudgetExhausted"])
        self.assertEqual(repository.events[-1].event_type, "mission.lifecycle.failed")
        self.assertEqual(
            repository.events[-1].causation_id,
            repository.events[-2].event_id,
        )

    def test_active_lease_cannot_be_recovered(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="LEASED",
                lease=Lease(
                    id="lease-active",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/work-units/wu-1/recover")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].status.value, "LEASED")
        self.assertEqual(repository.events, [])

    def test_runner_registers_and_lists_artifact_metadata_idempotently(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=1,
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )
        payload = {
            "id": "artifact-1",
            "leaseId": "lease-running",
            "kind": "diff",
            "digest": "sha256:" + "a" * 64,
            "contentAddress": "local:sha256/" + "a" * 64,
            "mediaType": "text/x-diff",
            "sizeBytes": 128,
        }

        first = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/artifacts",
            json=payload,
        )
        second = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/artifacts",
            json=payload,
        )
        listed = client.get("/api/v1/missions/mis-1/artifacts")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json()["attempt"], 1)
        self.assertEqual(len(repository.artifacts), 1)
        self.assertEqual(len(repository.events), 1)
        self.assertEqual(
            repository.events[0].event_type,
            "artifact.lifecycle.registered",
        )
        self.assertEqual(repository.events[0].aggregate_type.value, "artifact")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["artifacts"], [first.json()])

    def test_artifact_registration_rejects_invalid_lease_and_content_address(
        self,
    ) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=1,
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )
        payload = {
            "id": "artifact-1",
            "leaseId": "wrong-lease",
            "kind": "diff",
            "digest": "sha256:" + "a" * 64,
            "contentAddress": "local:sha256/" + "a" * 64,
            "mediaType": "text/x-diff",
            "sizeBytes": 128,
        }

        wrong_lease = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/artifacts",
            json=payload,
        )
        missing_digest = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/artifacts",
            json={
                **payload,
                "leaseId": "lease-running",
                "contentAddress": "local:artifacts/artifact-1",
            },
        )

        self.assertEqual(wrong_lease.status_code, 409)
        self.assertEqual(missing_digest.status_code, 409)
        self.assertEqual(repository.artifacts, [])
        self.assertEqual(repository.events, [])

    def test_artifact_registration_rejects_conflicting_id(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=1,
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )
        payload = {
            "id": "artifact-1",
            "leaseId": "lease-running",
            "kind": "diff",
            "digest": "sha256:" + "a" * 64,
            "contentAddress": "local:sha256/" + "a" * 64,
            "mediaType": "text/x-diff",
            "sizeBytes": 128,
        }

        created = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/artifacts",
            json=payload,
        )
        conflict = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/artifacts",
            json={**payload, "sizeBytes": 256},
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(len(repository.artifacts), 1)
        self.assertEqual(len(repository.events), 1)

    def test_complete_moves_running_work_unit_to_verifying(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=1,
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        repository.artifacts = [build_artifact()]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/complete",
            json={
                "leaseId": "lease-running",
                "artifactRefs": [
                    {
                        "id": "artifact-1",
                        "digest": "sha256:" + "a" * 64,
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "VERIFYING")
        self.assertIsNone(repository.work_units[0].lease)
        self.assertEqual(
            repository.events[-1].event_type,
            "work_unit.lifecycle.completed",
        )
        self.assertEqual(
            repository.events[-1].payload["artifactRefs"][0]["id"],
            "artifact-1",
        )

    def test_complete_rejects_unregistered_artifact(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=1,
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/complete",
            json={
                "leaseId": "lease-running",
                "artifactRefs": [
                    {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                ],
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].status.value, "RUNNING")
        self.assertIsNotNone(repository.work_units[0].lease)
        self.assertEqual(repository.events, [])

    def test_complete_requires_artifact_refs(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/complete",
            json={"leaseId": "lease-running", "artifactRefs": []},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(repository.work_units[0].status.value, "RUNNING")
        self.assertEqual(repository.events, [])

    def test_independent_verifier_passes_work_unit_and_mission(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        artifact_byte_verifier = FakeArtifactByteVerifier()
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=artifact_byte_verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "PASS",
                "artifactRefs": [
                    {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                ],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["evidence"]["verdict"], "PASS")
        self.assertEqual(body["workUnit"]["status"], "SUCCEEDED")
        self.assertEqual(body["mission"]["status"], "SUCCEEDED")
        self.assertEqual(
            artifact_byte_verifier.calls,
            [[repository.artifacts[0]]],
        )
        self.assertEqual(repository.evidence, [Evidence.model_validate(body["evidence"])])
        self.assertEqual(
            [event.event_type for event in repository.events],
            [
                "evidence.lifecycle.recorded",
                "work_unit.lifecycle.verified",
                "mission.lifecycle.verifying",
                "mission.lifecycle.succeeded",
            ],
        )
        listed = client.get("/api/v1/missions/mis-1/evidence")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["evidence"], [body["evidence"]])

    def test_evidence_rejects_artifact_from_another_work_unit(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(id="wu-1", status="VERIFYING"),
            build_work_unit(id="wu-2", status="VERIFYING"),
        ]
        repository.artifacts = [build_artifact(work_unit_id="wu-2")]
        verifier = FakeArtifactByteVerifier()
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "PASS",
                "artifactRefs": [
                    {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                ],
                "summary": "Wrong WorkUnit artifact.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("another work unit", response.json()["detail"])
        self.assertEqual(verifier.calls, [])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_delegated_work_unit_closes_artifact_evidence_and_mission(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(id="wu-parent", status="SUCCEEDED"),
            build_work_unit(
                id="wu-child",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            ),
        ]
        runner_client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        claimed = runner_client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={"agentId": "reviewer", "adapterType": "local_codex"},
        )
        self.assertEqual(claimed.status_code, 200)
        lease_id = claimed.json()["workUnit"]["lease"]["id"]
        started = runner_client.post(
            "/api/v1/missions/mis-1/work-units/wu-child/start",
            json={"leaseId": lease_id},
        )
        self.assertEqual(started.status_code, 200)

        digest = "sha256:" + "a" * 64
        registered = runner_client.post(
            "/api/v1/missions/mis-1/work-units/wu-child/artifacts",
            json={
                "id": "artifact-child",
                "leaseId": lease_id,
                "kind": "test-result",
                "digest": digest,
                "contentAddress": "local:sha256/" + "a" * 64,
                "mediaType": "text/plain",
                "sizeBytes": 12,
            },
        )
        self.assertEqual(registered.status_code, 201)
        completed = runner_client.post(
            "/api/v1/missions/mis-1/work-units/wu-child/complete",
            json={
                "leaseId": lease_id,
                "artifactRefs": [{"id": "artifact-child", "digest": digest}],
            },
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "VERIFYING")

        verifier = FakeArtifactByteVerifier()
        verifier_client = TestClient(
            build_app(
                repository,
                {"id": "admin-1", "name": "Verifier", "role": "admin"},
                artifact_byte_verifier=verifier,
            )
        )
        evidence = verifier_client.post(
            "/api/v1/missions/mis-1/work-units/wu-child/verify",
            json={
                "criterionId": "tests",
                "verifierId": "admin-1",
                "verifierVersion": "1.0",
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-child", "digest": digest}],
                "summary": "Delegated artifact verified.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(evidence.status_code, 200)
        self.assertEqual(evidence.json()["workUnit"]["status"], "SUCCEEDED")
        self.assertEqual(evidence.json()["evidence"]["workUnitId"], "wu-child")
        self.assertEqual(evidence.json()["mission"]["status"], "SUCCEEDED")
        self.assertEqual(repository.artifacts[0].work_unit_id, "wu-child")

    def test_expired_delegated_claim_recovers_and_can_be_reclaimed(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(id="wu-parent", status="SUCCEEDED"),
            build_work_unit(
                id="wu-child",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            ),
        ]
        runner_client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )
        claimed = runner_client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={"agentId": "reviewer", "adapterType": "local_codex"},
        )
        self.assertEqual(claimed.status_code, 200)
        lease_id = claimed.json()["workUnit"]["lease"]["id"]
        child = repository.work_units[1]
        repository.work_units[1] = child.model_copy(
            update={
                "lease": Lease(
                    id=lease_id,
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                )
            }
        )

        recovered = runner_client.post(
            "/api/v1/missions/mis-1/work-units/wu-child/recover"
        )
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["status"], "RETRYING")
        reclaimed = runner_client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={"agentId": "reviewer", "adapterType": "local_codex"},
        )
        self.assertEqual(reclaimed.status_code, 200)
        self.assertEqual(reclaimed.json()["workUnit"]["status"], "LEASED")
        self.assertEqual(reclaimed.json()["workUnit"]["attempt"], 2)

    def test_unavailable_artifact_bytes_fail_without_state_changes(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        initial_mission = repository.mission
        initial_work_unit = repository.work_units[0]
        verifier = FakeArtifactByteVerifier(
            error=ArtifactBytesUnavailableError("artifact store unavailable")
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "PASS",
                "artifactRefs": [
                    {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                ],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.json()["detail"], "artifact store unavailable")
        self.assertEqual(repository.mission, initial_mission)
        self.assertEqual(repository.work_units, [initial_work_unit])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_artifact_digest_mismatch_fails_without_state_changes(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        initial_mission = repository.mission
        initial_work_unit = repository.work_units[0]
        verifier = FakeArtifactByteVerifier(
            error=ArtifactIntegrityError("artifact byte digest does not match")
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "PASS",
                "artifactRefs": [
                    {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                ],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            "artifact byte digest does not match",
        )
        self.assertEqual(repository.mission, initial_mission)
        self.assertEqual(repository.work_units, [initial_work_unit])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_artifact_metadata_is_revalidated_after_byte_verification(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        initial_mission = repository.mission
        initial_work_unit = repository.work_units[0]

        def replace_artifact(_: list[Artifact]) -> None:
            repository.artifacts[0] = build_artifact(size_bytes=129)

        verifier = FakeArtifactByteVerifier(on_verify=replace_artifact)
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "PASS",
                "artifactRefs": [
                    {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                ],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            "artifact metadata changed during byte verification",
        )
        self.assertEqual(repository.mission, initial_mission)
        self.assertEqual(repository.work_units, [initial_work_unit])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_mission_success_uses_unbounded_passed_criterion_projection(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        repository.evidence = [
            build_evidence(
                id=f"evd-history-{index}",
                criterion_id=f"legacy-{index}",
            )
            for index in range(200)
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "PASS",
                "artifactRefs": [
                    {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                ],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mission"]["status"], "SUCCEEDED")
        self.assertEqual(len(repository.evidence), 201)

    def test_mission_waits_for_all_required_criteria(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract(
            acceptance_criteria=[
                {
                    "id": "tests",
                    "kind": "test",
                    "description": "Tests pass",
                    "required": True,
                },
                {
                    "id": "security",
                    "kind": "security",
                    "description": "Security scan passes",
                    "required": True,
                },
            ]
        )
        repository.work_units = [
            build_work_unit(id="wu-tests", status="VERIFYING"),
            build_work_unit(id="wu-security", status="VERIFYING"),
        ]
        tests_digest = "sha256:" + "a" * 64
        security_digest = "sha256:" + "c" * 64
        repository.artifacts = [
            build_artifact(
                id="artifact-tests",
                work_unit_id="wu-tests",
                digest=tests_digest,
                content_address="local:sha256/" + "a" * 64,
            ),
            build_artifact(
                id="artifact-security",
                work_unit_id="wu-security",
                digest=security_digest,
                content_address="local:sha256/" + "c" * 64,
            ),
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
            )
        )
        request = {
            "verifierId": "verifier-1",
            "verifierVersion": "9.0",
            "verdict": "PASS",
            "summary": "Verification passed.",
            "integrityHash": "sha256:" + "b" * 64,
        }

        first = client.post(
            "/api/v1/missions/mis-1/work-units/wu-tests/verify",
            json={
                **request,
                "criterionId": "tests",
                "artifactRefs": [{"id": "artifact-tests", "digest": tests_digest}],
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["workUnit"]["status"], "SUCCEEDED")
        self.assertEqual(first.json()["mission"]["status"], "RUNNING")

        second = client.post(
            "/api/v1/missions/mis-1/work-units/wu-security/verify",
            json={
                **request,
                "criterionId": "security",
                "artifactRefs": [
                    {"id": "artifact-security", "digest": security_digest}
                ],
            },
        )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["workUnit"]["status"], "SUCCEEDED")
        self.assertEqual(second.json()["mission"]["status"], "SUCCEEDED")

    def test_inconclusive_evidence_does_not_claim_success(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "INCONCLUSIVE",
                "artifactRefs": [
                    {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                ],
                "summary": "The test environment was unavailable.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workUnit"]["status"], "VERIFYING")
        self.assertEqual(response.json()["mission"]["status"], "RUNNING")
        self.assertEqual(len(repository.evidence), 1)
        self.assertEqual(
            repository.events[-1].event_type,
            "work_unit.lifecycle.verification_inconclusive",
        )

    def test_failed_evidence_fails_work_unit_and_mission(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "FAIL",
                "artifactRefs": [
                    {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                ],
                "summary": "A required test failed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workUnit"]["status"], "FAILED")
        self.assertEqual(response.json()["mission"]["status"], "FAILED")
        self.assertEqual(len(repository.evidence), 1)
        self.assertEqual(
            [event.event_type for event in repository.events],
            [
                "evidence.lifecycle.recorded",
                "work_unit.lifecycle.verification_failed",
                "mission.lifecycle.failed",
            ],
        )
        self.assertEqual(repository.events[-1].payload["workUnitId"], "wu-1")
        self.assertEqual(
            repository.events[-1].causation_id,
            repository.events[-2].event_id,
        )

    def test_non_verifier_cannot_record_evidence(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "pytest",
                "verifierVersion": "9.0",
                "verdict": "PASS",
                "artifactRefs": [
                    {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                ],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(repository.events, [])

    def test_verifier_cannot_impersonate_another_verifier(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "other-verifier",
                "verifierVersion": "9.0",
                "verdict": "PASS",
                "artifactRefs": [
                    {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                ],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(repository.events, [])

    def test_fail_and_retry_require_lease_and_respect_retry_budget(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                lease=Lease(
                    id="lease-fail",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        failed = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/fail",
            json={"leaseId": "lease-fail", "reason": "runner error"},
        )
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()["status"], "FAILED")
        self.assertEqual(repository.mission.status.value, "FAILED")
        self.assertEqual(repository.events[-2].payload["reason"], "runner error")
        self.assertEqual(repository.events[-1].event_type, "mission.lifecycle.failed")
        self.assertEqual(repository.events[-1].payload["reason"], "runner error")

        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units[0] = build_work_unit(
            status="RUNNING",
            attempt=3,
            lease=Lease(
                id="lease-exhausted",
                runner_id="user-1",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ),
        )
        before_events = len(repository.events)
        exhausted = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/retry",
            json={"leaseId": "lease-exhausted", "reason": "retry"},
        )
        self.assertEqual(exhausted.status_code, 409)
        self.assertEqual(len(repository.events), before_events)


if __name__ == "__main__":
    unittest.main()
