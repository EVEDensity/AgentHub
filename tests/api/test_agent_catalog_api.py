from __future__ import annotations

import unittest
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.agent_catalog import (
    get_agent_catalog_synchronizer,
    get_agent_catalog_writer,
    router,
)
from app.services.agent_binding_service import (
    AgentBinding,
    AgentBindingUnavailableError,
    AgentCatalogRecord,
    AgentCatalogVersionConflictError,
)
from app.services.agent_catalog_sync_service import (
    RegistryAgentNotFoundError,
    RegistryAgentNotRunnableError,
)
from app.services.auth_service import get_current_user


class FakeAgentCatalogWriter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def put(self, **values: Any) -> AgentCatalogRecord:
        self.calls.append(values)
        if self.error is not None:
            raise self.error
        return AgentCatalogRecord(
            scope_id=values["scope_id"],
            binding=AgentBinding(
                agent_id=values["agent_id"],
                adapter_type=values["adapter_type"],
                capabilities=tuple(sorted(set(values["capabilities"]))),
            ),
            enabled=values["enabled"],
            source_version=values["expected_version"] + 1,
            updated_at="2026-08-14T10:00:00+00:00",
        )


class FakeAgentCatalogSynchronizer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def sync(self, **values: Any) -> AgentCatalogRecord:
        self.calls.append(values)
        if self.error is not None:
            raise self.error
        return AgentCatalogRecord(
            scope_id=values["scope_id"],
            binding=AgentBinding(
                agent_id=values["agent_id"],
                adapter_type="local_codex",
                capabilities=("repository.read",),
            ),
            enabled=True,
            source_version=values["expected_version"] + 1,
            updated_at="2026-08-14T10:00:00+00:00",
        )


def build_app(
    writer: FakeAgentCatalogWriter,
    user: dict[str, Any],
    *,
    synchronizer: FakeAgentCatalogSynchronizer | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_agent_catalog_writer] = lambda: writer
    if synchronizer is not None:
        app.dependency_overrides[get_agent_catalog_synchronizer] = lambda: synchronizer
    app.dependency_overrides[get_current_user] = lambda: user
    return app


class AgentCatalogApiTests(unittest.TestCase):
    def test_workspace_owner_can_create_safe_binding(self) -> None:
        writer = FakeAgentCatalogWriter()
        client = TestClient(
            build_app(
                writer,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.put(
            "/api/v1/agent-catalog/workspaces/workspace-1/bindings/reviewer",
            json={
                "adapterType": "local_codex",
                "capabilities": ["repository.read"],
                "enabled": True,
                "expectedVersion": 0,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sourceVersion"], 1)
        self.assertEqual(writer.calls[0]["scope_id"], "workspace-1")

    def test_non_member_cannot_write_workspace_binding(self) -> None:
        writer = FakeAgentCatalogWriter()
        client = TestClient(
            build_app(
                writer,
                {"id": "user-2", "name": "Grace", "role": "developer"},
            )
        )

        response = client.put(
            "/api/v1/agent-catalog/workspaces/workspace-1/bindings/reviewer",
            json={"adapterType": "local_codex", "expectedVersion": 0},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(writer.calls, [])

    def test_sensitive_registry_fields_are_rejected_before_write(self) -> None:
        writer = FakeAgentCatalogWriter()
        client = TestClient(
            build_app(
                writer,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.put(
            "/api/v1/agent-catalog/workspaces/workspace-1/bindings/reviewer",
            json={
                "adapterType": "local_codex",
                "expectedVersion": 0,
                "apiKey": "must-not-enter-catalog",
                "baseUrl": "https://provider.invalid",
                "config": {"secret": True},
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(writer.calls, [])

    def test_version_conflict_returns_409(self) -> None:
        writer = FakeAgentCatalogWriter(
            AgentCatalogVersionConflictError("Agent catalog binding version conflict")
        )
        client = TestClient(
            build_app(
                writer,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.put(
            "/api/v1/agent-catalog/workspaces/workspace-1/bindings/reviewer",
            json={"adapterType": "local_codex", "expectedVersion": 4},
        )

        self.assertEqual(response.status_code, 409)

    def test_catalog_unavailability_returns_503(self) -> None:
        writer = FakeAgentCatalogWriter(
            AgentBindingUnavailableError("Agent catalog write failed")
        )
        client = TestClient(
            build_app(
                writer,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.put(
            "/api/v1/agent-catalog/workspaces/workspace-1/bindings/reviewer",
            json={"adapterType": "local_codex", "expectedVersion": 0},
        )

        self.assertEqual(response.status_code, 503)

    def test_sync_derives_registry_owner_from_authenticated_user(self) -> None:
        writer = FakeAgentCatalogWriter()
        synchronizer = FakeAgentCatalogSynchronizer()
        client = TestClient(
            build_app(
                writer,
                {"id": "user-1", "name": "Ada", "role": "admin"},
                synchronizer=synchronizer,
            )
        )

        response = client.post(
            "/api/v1/agent-catalog/workspaces/workspace-1/bindings/reviewer/sync",
            json={"expectedVersion": 2},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            synchronizer.calls,
            [
                {
                    "scope_id": "workspace-1",
                    "source_owner_id": "user-1",
                    "agent_id": "reviewer",
                    "expected_version": 2,
                }
            ],
        )

    def test_sync_missing_registry_agent_returns_404(self) -> None:
        synchronizer = FakeAgentCatalogSynchronizer(
            RegistryAgentNotFoundError("Registry Agent not found")
        )
        client = TestClient(
            build_app(
                FakeAgentCatalogWriter(),
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
                synchronizer=synchronizer,
            )
        )

        response = client.post(
            "/api/v1/agent-catalog/workspaces/workspace-1/bindings/missing/sync",
            json={"expectedVersion": 0},
        )

        self.assertEqual(response.status_code, 404)

    def test_sync_non_runnable_registry_agent_returns_409(self) -> None:
        synchronizer = FakeAgentCatalogSynchronizer(
            RegistryAgentNotRunnableError("Registry Agent has no executable adapter")
        )
        client = TestClient(
            build_app(
                FakeAgentCatalogWriter(),
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
                synchronizer=synchronizer,
            )
        )

        response = client.post(
            "/api/v1/agent-catalog/workspaces/workspace-1/bindings/reviewer/sync",
            json={"expectedVersion": 0},
        )

        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
