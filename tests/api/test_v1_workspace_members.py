"""Tests for GET /api/v1/workspaces/{scope_id}/members (P1 ADR-0108 §3.3).

Verifies the unified member roster endpoint: the requesting human is
always included, enabled agent catalog bindings come from the DB, and
unauthorized callers are rejected.
"""

from __future__ import annotations

import unittest
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import router as v1_router
from app.services.agent_binding_service import (
    AgentBinding,
    DatabaseAgentBindingResolver,
)
from app.services.auth_service import get_current_user


def build_app(user: dict[str, Any] | None) -> FastAPI:
    app = FastAPI()
    app.include_router(v1_router)
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    # Always override the resolver — these tests do not hit PostgreSQL.
    from app.api.v1.workspace_members import get_agent_binding_resolver

    class FakeResolver(DatabaseAgentBindingResolver):
        async def list_enabled(self, *, scope_id: str) -> list[AgentBinding]:
            return []

    app.dependency_overrides[get_agent_binding_resolver] = lambda: FakeResolver()
    return app


class WorkspaceMembersTests(unittest.TestCase):
    def test_human_member_always_included(self) -> None:
        user = {"id": "user-1", "name": "Ada", "role": "member"}
        client = TestClient(build_app(user))

        response = client.get("/api/v1/workspaces/user-1/members")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["scopeId"], "user-1")
        humans = [m for m in body["members"] if m["kind"] == "human"]
        self.assertEqual(len(humans), 1)
        self.assertEqual(humans[0]["memberId"], "user-1")
        self.assertEqual(humans[0]["name"], "Ada")

    def test_unauthorized_workspace_rejected(self) -> None:
        user = {"id": "user-1", "name": "Ada", "role": "member"}
        client = TestClient(build_app(user))

        response = client.get("/api/v1/workspaces/other-workspace/members")

        self.assertEqual(response.status_code, 403)

    def test_agents_appear_with_correct_shape(self) -> None:
        user = {"id": "user-1", "name": "Ada", "role": "member"}
        app = build_app(user)
        # Override the resolver to return deterministic bindings without DB
        from app.api.v1.workspace_members import get_agent_binding_resolver

        class FakeResolver(DatabaseAgentBindingResolver):
            async def list_enabled(self, *, scope_id: str) -> list[AgentBinding]:
                return [
                    AgentBinding(
                        agent_id="CodeGen",
                        adapter_type="desktop.local",
                        capabilities=("code-generation",),
                    ),
                    AgentBinding(
                        agent_id="Review",
                        adapter_type="desktop.local",
                        capabilities=("code-review",),
                    ),
                ]

        app.dependency_overrides[get_agent_binding_resolver] = lambda: FakeResolver()
        client = TestClient(app)

        response = client.get("/api/v1/workspaces/user-1/members")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        agents = [m for m in body["members"] if m["kind"] == "agent"]
        self.assertEqual(len(agents), 2)
        self.assertEqual({a["memberId"] for a in agents}, {"CodeGen", "Review"})
        for agent in agents:
            self.assertEqual(agent["role"], "agent")
            self.assertEqual(agent["adapterType"], "desktop.local")
            self.assertIsInstance(agent["capabilities"], list)

    def test_no_user_returns_401(self) -> None:
        client = TestClient(build_app(None))

        response = client.get("/api/v1/workspaces/user-1/members")

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
