from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.missions import get_mission_repository, router
from app.domain import EventEnvelope, Mission, MissionContract, WorkUnit
from app.services.auth_service import get_current_user
from tests.domain.factories import (
    build_contract,
    build_event,
    build_mission,
    build_work_unit,
)


class FakeMissionRepository:
    def __init__(self) -> None:
        self.contract: MissionContract | None = None
        self.mission: Mission | None = None
        self.events: list[EventEnvelope] = []
        self.list_result: list[Mission] = []
        self.work_units: list[WorkUnit] = []

    @asynccontextmanager
    async def transaction(self):
        yield self

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

    async def get_mission_for_update(self, mission_id: str) -> Mission | None:
        return await self.get_mission(mission_id)

    async def update_mission(self, mission: Mission) -> None:
        self.mission = mission

    async def get_last_event_sequence(self, mission_id: str) -> int:
        sequences = [
            event.sequence
            for event in self.events
            if event.aggregate_type.value == "mission"
            and event.aggregate_id == mission_id
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

    async def append_event(self, event: EventEnvelope) -> None:
        self.events.append(event)


def build_app(repository: FakeMissionRepository, user: dict[str, Any]) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_mission_repository] = lambda: repository
    app.dependency_overrides[get_current_user] = lambda: user
    return app


class MissionApiTests(unittest.TestCase):
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
        self.assertEqual(event.event_type, "workunit.lifecycle.created")
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


if __name__ == "__main__":
    unittest.main()
