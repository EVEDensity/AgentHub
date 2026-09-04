from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import permissions
from app.services.auth_service import get_current_user


def test_permission_policy_is_authenticated_and_explains_source(monkeypatch):
    app = FastAPI()
    app.include_router(permissions.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "role": "developer"}
    async def fetch(*args):
        return [{"id": 1, "agent_id": "u1", "tool_pattern": "shell", "path_pattern": "src/*", "behavior": "deny", "source": "org", "priority": 50, "enabled": 1}]
    monkeypatch.setattr(permissions, "afetch_all", fetch)
    with TestClient(app) as client:
        response = client.get("/api/v1/permissions/policy")
    assert response.status_code == 200
    assert response.json()["rules"][0]["source"] == "org"


def test_permission_sync_rejects_other_user_scope(monkeypatch):
    app = FastAPI()
    app.include_router(permissions.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "role": "developer"}
    async def execute(*args):
        return None
    monkeypatch.setattr(permissions, "aexecute", execute)
    with TestClient(app) as client:
        response = client.put("/api/v1/permissions/policy", json={"rules": [{"agent_id": "u2", "tool_pattern": "*", "behavior": "deny"}]})
    assert response.status_code == 403
