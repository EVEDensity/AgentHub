from __future__ import annotations

import unittest
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.a2a_adapter import (
    get_a2a_binding_selector,
    get_a2a_repository,
    router,
)
from app.services.agent_binding_service import (
    AgentBinding,
    AgentBindingSelector,
    StaticAgentBindingSelector,
    UnavailableAgentBindingSelector,
)
from app.services.auth_service import get_current_user
from tests.api.test_missions_api import FakeMissionRepository


def build_app(
    repository: FakeMissionRepository,
    user: dict[str, Any],
    *,
    binding_selector: AgentBindingSelector | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_a2a_repository] = lambda: repository
    app.dependency_overrides[get_a2a_binding_selector] = lambda: (
        binding_selector
        if binding_selector is not None
        else StaticAgentBindingSelector(
            {
                "user-1": [
                    AgentBinding(
                        agent_id="inbound-reviewer",
                        adapter_type="local_codex",
                        capabilities=("a2a.receive", "code_generation"),
                    )
                ]
            }
        )
    )
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
        self.inbound_request = {
            "taskId": "inbound-task-1",
            "workspaceId": "user-1",
            "objective": "Review the inbound change",
            "sourceAgentUrl": "https://sender.example.test",
            "requiredCapabilities": ["code_generation"],
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

    def test_inbound_accept_creates_catalog_bound_local_work_unit(self) -> None:
        response = self.client.post(
            "/api/v1/a2a/tasks/inbound",
            json=self.inbound_request,
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["taskId"], "inbound-task-1")
        self.assertEqual(body["state"], "submitted")
        self.assertEqual(body["missionStatus"], "RUNNING")
        self.assertEqual(body["workUnitStatus"], "PENDING")
        self.assertIsNotNone(self.repository.mission)
        self.assertEqual(self.repository.mission.source.type.value, "a2a.inbound")
        self.assertEqual(
            self.repository.mission.source.reference,
            "https://sender.example.test",
        )
        self.assertEqual(len(self.repository.work_units), 1)
        work_unit = self.repository.work_units[0]
        self.assertEqual(work_unit.kind, "a2a.inbound")
        self.assertEqual(work_unit.assigned_agent_id, "inbound-reviewer")
        self.assertEqual(work_unit.assigned_adapter, "local_codex")
        self.assertEqual(
            work_unit.required_capabilities,
            ("a2a.receive", "code_generation"),
        )
        capabilities = {
            grant.capability for grant in self.repository.contract.allowed_capabilities
        }
        self.assertEqual(capabilities, {"a2a.receive", "code_generation"})
        created_event = self.repository.events[-1]
        self.assertEqual(created_event.payload["assignedAgentId"], "inbound-reviewer")
        self.assertEqual(created_event.payload["assignedAdapter"], "local_codex")

    def test_inbound_accept_is_idempotent_for_same_source(self) -> None:
        first = self.client.post(
            "/api/v1/a2a/tasks/inbound",
            json=self.inbound_request,
        )
        event_count = len(self.repository.events)
        repeated = self.client.post(
            "/api/v1/a2a/tasks/inbound",
            json=self.inbound_request,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["missionId"], first.json()["missionId"])
        self.assertEqual(len(self.repository.work_units), 1)
        self.assertEqual(len(self.repository.events), event_count)

    def test_inbound_retry_preserves_original_binding_snapshot(self) -> None:
        app = build_app(
            self.repository,
            {"id": "user-1", "name": "Ada", "role": "developer"},
            binding_selector=StaticAgentBindingSelector(
                {
                    "user-1": [
                        AgentBinding(
                            agent_id="original-reviewer",
                            adapter_type="local_codex",
                            capabilities=("a2a.receive", "code_generation"),
                        )
                    ]
                }
            ),
        )
        client = TestClient(app)
        first = client.post("/api/v1/a2a/tasks/inbound", json=self.inbound_request)
        app.dependency_overrides[get_a2a_binding_selector] = (
            UnavailableAgentBindingSelector
        )

        repeated = client.post(
            "/api/v1/a2a/tasks/inbound",
            json=self.inbound_request,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(
            self.repository.work_units[0].assigned_agent_id,
            "original-reviewer",
        )

    def test_inbound_without_matching_binding_has_no_persistence_side_effects(
        self,
    ) -> None:
        client = TestClient(
            build_app(
                self.repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
                binding_selector=StaticAgentBindingSelector({}),
            )
        )

        response = client.post(
            "/api/v1/a2a/tasks/inbound",
            json=self.inbound_request,
        )

        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.repository.mission)
        self.assertIsNone(self.repository.contract)
        self.assertEqual(self.repository.work_units, [])
        self.assertEqual(self.repository.events, [])

    def test_inbound_catalog_failure_has_no_persistence_side_effects(self) -> None:
        client = TestClient(
            build_app(
                self.repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
                binding_selector=UnavailableAgentBindingSelector(),
            )
        )

        response = client.post(
            "/api/v1/a2a/tasks/inbound",
            json=self.inbound_request,
        )

        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.repository.mission)
        self.assertIsNone(self.repository.contract)
        self.assertEqual(self.repository.work_units, [])
        self.assertEqual(self.repository.events, [])

    def test_inbound_rejects_capability_incomplete_selector_result(self) -> None:
        class IncompleteSelector:
            async def select(
                self,
                *,
                scope_id: str,
                required_capabilities: Sequence[str],
            ) -> AgentBinding:
                del scope_id, required_capabilities
                return AgentBinding(
                    agent_id="incomplete-reviewer",
                    adapter_type="local_codex",
                    capabilities=("a2a.receive",),
                )

        client = TestClient(
            build_app(
                self.repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
                binding_selector=IncompleteSelector(),
            )
        )

        response = client.post(
            "/api/v1/a2a/tasks/inbound",
            json=self.inbound_request,
        )

        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.repository.mission)
        self.assertEqual(self.repository.events, [])

    def test_inbound_task_identity_is_isolated_by_source_origin(self) -> None:
        first = self.client.post(
            "/api/v1/a2a/tasks/inbound",
            json=self.inbound_request,
        )
        second = self.client.post(
            "/api/v1/a2a/tasks/inbound",
            json={
                **self.inbound_request,
                "sourceAgentUrl": "HTTPS://Different-Sender.Example.Test/",
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first.json()["missionId"], second.json()["missionId"])
        self.assertEqual(len(self.repository.work_units), 2)
        self.assertEqual(
            self.repository.mission.source.reference,
            "https://different-sender.example.test",
        )

    def test_inbound_cancel_uses_inbound_mission_truth(self) -> None:
        self.client.post(
            "/api/v1/a2a/tasks/inbound",
            json=self.inbound_request,
        )
        cancelled = self.client.post(
            "/api/v1/a2a/tasks/inbound/cancel",
            json={
                "workspaceId": "user-1",
                "sourceAgentUrl": "https://sender.example.test",
                "taskId": "inbound-task-1",
            },
        )

        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["state"], "canceled")
        self.assertEqual(cancelled.json()["missionStatus"], "CANCELLED")
        self.assertEqual(cancelled.json()["workUnitStatus"], "CANCELLED")

    def test_inbound_cancel_cannot_cross_source_origin(self) -> None:
        self.client.post(
            "/api/v1/a2a/tasks/inbound",
            json=self.inbound_request,
        )
        cancelled = self.client.post(
            "/api/v1/a2a/tasks/inbound/cancel",
            json={
                "workspaceId": "user-1",
                "sourceAgentUrl": "https://different-sender.example.test",
                "taskId": "inbound-task-1",
            },
        )

        self.assertEqual(cancelled.status_code, 404)
        self.assertEqual(self.repository.mission.status.value, "RUNNING")

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
        self.assertEqual(cancelled.json()["workUnitStatus"], "CANCELLED")
        self.assertEqual(self.repository.mission.status.value, "CANCELLED")

    def test_dispatch_failure_fails_work_unit_and_mission_with_reason(self) -> None:
        self.client.post("/api/v1/a2a/tasks", json=self.request)

        failed = self.client.post(
            "/api/v1/a2a/tasks/fail",
            json={
                "workspaceId": "user-1",
                "taskId": "external-task-1",
                "reason": "remote agent refused the request",
            },
        )

        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()["state"], "failed")
        self.assertEqual(failed.json()["missionStatus"], "FAILED")
        self.assertEqual(failed.json()["workUnitStatus"], "FAILED")
        self.assertEqual(self.repository.events[-2].event_type, "work_unit.lifecycle.failed")
        self.assertEqual(self.repository.events[-1].event_type, "mission.lifecycle.failed")
        self.assertEqual(
            self.repository.events[-1].payload["reason"],
            "remote agent refused the request",
        )

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
