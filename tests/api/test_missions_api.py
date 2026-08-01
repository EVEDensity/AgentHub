from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.missions import get_mission_repository, router
from app.domain import EventEnvelope, Mission, MissionContract
from app.services.auth_service import get_current_user
from tests.domain.factories import build_contract, build_mission


class FakeMissionRepository:
    def __init__(self) -> None:
        self.contract: MissionContract | None = None
        self.mission: Mission | None = None
        self.events: list[EventEnvelope] = []
        self.list_result: list[Mission] = []

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


if __name__ == "__main__":
    unittest.main()
