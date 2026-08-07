from __future__ import annotations

import unittest
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.a2a_adapter import get_a2a_repository, router
from app.services.auth_service import get_current_user
from tests.api.test_missions_api import FakeMissionRepository


def build_app(repository: FakeMissionRepository, user: dict[str, Any]) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_a2a_repository] = lambda: repository
    app.dependency_overrides[get_current_user] = lambda: user
    return app


class A2AAdapterApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeMissionRepository()
        self.client = TestClient(
            build_app(
                self.repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )
        self.request = {
            "taskId": "external-task-1",
            "workspaceId": "user-1",
            "objective": "Delegate a durable task",
            "agentUrl": "https://agent.example.test/a2a",
            "requiredCapabilities": ["artifact.write"],
        }

    def test_submit_creates_started_mission_and_pending_work_unit(self) -> None:
        response = self.client.post("/api/v1/a2a/tasks", json=self.request)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["taskId"], "external-task-1")
        self.assertEqual(body["state"], "submitted")
        self.assertEqual(body["missionStatus"], "RUNNING")
        self.assertEqual(body["workUnitStatus"], "PENDING")
        self.assertIsNotNone(self.repository.mission)
        self.assertEqual(self.repository.mission.source.type.value, "a2a")
        self.assertEqual(self.repository.mission.source.external_id, "external-task-1")
        self.assertEqual(self.repository.mission.created_by.type.value, "adapter")
        self.assertIsNotNone(self.repository.contract)
        capabilities = {
            grant.capability for grant in self.repository.contract.allowed_capabilities
        }
        self.assertEqual(capabilities, {"a2a.send", "artifact.write"})
        self.assertEqual(len(self.repository.work_units), 1)
        self.assertEqual(len(self.repository.events), 3)

    def test_submit_is_idempotent_and_rejects_changed_intent(self) -> None:
        first = self.client.post("/api/v1/a2a/tasks", json=self.request)
        event_count = len(self.repository.events)

        repeated = self.client.post("/api/v1/a2a/tasks", json=self.request)
        changed = self.client.post(
            "/api/v1/a2a/tasks",
            json={**self.request, "objective": "A different task"},
        )
        changed_contract = self.client.post(
            "/api/v1/a2a/tasks",
            json={**self.request, "retries": 5},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["missionId"], first.json()["missionId"])
        self.assertEqual(len(self.repository.work_units), 1)
        self.assertEqual(len(self.repository.events), event_count)
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed_contract.status_code, 409)

    def test_get_and_cancel_use_mission_as_the_task_truth(self) -> None:
        self.client.post("/api/v1/a2a/tasks", json=self.request)

        fetched = self.client.get(
            "/api/v1/a2a/tasks",
            params={"workspaceId": "user-1", "taskId": "external-task-1"},
        )
        cancelled = self.client.post(
            "/api/v1/a2a/tasks/cancel",
            json={"workspaceId": "user-1", "taskId": "external-task-1"},
        )

        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["missionStatus"], "RUNNING")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["state"], "canceled")
        self.assertEqual(cancelled.json()["missionStatus"], "CANCELLED")
        self.assertEqual(self.repository.mission.status.value, "CANCELLED")

    def test_unknown_and_cross_workspace_tasks_are_not_exposed(self) -> None:
        missing = self.client.get(
            "/api/v1/a2a/tasks",
            params={"workspaceId": "user-1", "taskId": "missing"},
        )
        denied = self.client.get(
            "/api/v1/a2a/tasks",
            params={"workspaceId": "workspace-2", "taskId": "missing"},
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
