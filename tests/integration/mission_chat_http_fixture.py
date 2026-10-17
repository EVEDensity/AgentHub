"""Disposable real Mission chat HTTP server; run as a module, never in production."""
from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from unittest.mock import patch

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1 import access, chat_mission, execution_status, missions, sessions, workspace_members
from app.core.config import ArtifactStoreSettings
from app.db.init_db import _ainit_sqlite
from app.db.sqlite_pool import SQLitePool
from app.services.auth_service import get_current_user
from app.services.artifact_integrity_service import ContentAddressedArtifactByteVerifier
from app.services.workspace_context import workspace_root_override
from tests.integration.mission_chat_driver import AGENT_ID, WORKSPACE_ID, MissionChatDriver

HOST, PORT = "127.0.0.1", 38100
_SECURITY = HTTPBearer(auto_error=False)
_RULES = """rules:
  - id: browser-confirm
    description: 浏览器测试规则需要确认
    trigger:
      kind: keyword
      keywords: [fixture-confirm]
    action:
      kind: create_mission
      target_agent: local-desktop-agent
      require_confirmation: true
"""


async def fixture_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_SECURITY)],
) -> dict:
    if credentials and credentials.scheme.lower() == "bearer":
        if credentials.credentials == "chat-browser-human":
            return {"id": WORKSPACE_ID, "role": "developer", "name": "Chat fixture human"}
        if credentials.credentials == "chat-browser-foreign":
            return {"id": "workspace-chat-foreign", "role": "developer", "name": "Foreign human"}
    raise HTTPException(401, "Fixture authentication required")


class AdvanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    action: Literal["start", "text", "expire", "complete", "decision"]
    lease_seconds: Annotated[int, Field(alias="leaseSeconds", ge=1, le=60)] = 60


def build_fixture_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        import app.db.session as database

        with tempfile.TemporaryDirectory(prefix="agenthub-mission-chat-browser-") as temporary:
            root = Path(temporary)
            pool = SQLitePool(root / "missions.sqlite3")
            await pool.initialize()

            async def owned_pool():
                return pool

            try:
                # Every default repository, including the catalog's list query
                # and transaction companions, uses this real private database.
                with ExitStack() as stack:
                    stack.enter_context(patch.object(database, "aget_pool", owned_pool))
                    stack.enter_context(patch.object(database, "DB_BACKEND", "sqlite"))
                    await _ainit_sqlite()
                    await database.aexecute(
                        "INSERT INTO agent_catalog_bindings(scope_id,agent_id,adapter_type,capabilities,enabled) "
                        "VALUES($1,$2,$3,$4,TRUE)", WORKSPACE_ID, AGENT_ID, "function-calling", "[]",
                    )
                    workspace = root / "workspace"
                    (workspace / ".agenthub").mkdir(parents=True)
                    (workspace / ".agenthub" / "rules.yaml").write_text(_RULES, encoding="utf-8")
                    app.state.workspace = workspace
                    app.state.root = root
                    app.state.commands = Counter()
                    store = ArtifactStoreSettings(
                        backend="local", local_root=root / "cas", verify_max_bytes=262144,
                    )
                    app.state.driver = MissionChatDriver(store)
                    exporter = ContentAddressedArtifactByteVerifier(store)
                    app.dependency_overrides[missions.get_mission_artifact_byte_exporter] = lambda: exporter
                    app.dependency_overrides[missions.get_artifact_byte_verifier] = lambda: exporter
                    print(json.dumps({"port": PORT, "workspaceId": WORKSPACE_ID,
                                      "fixturePid": os.getpid()}), flush=True)
                    yield
            finally:
                await pool.close()

    app = FastAPI(lifespan=lifespan)
    for router in (access.router, chat_mission.router, missions.router,
                   execution_status.router, sessions.router, workspace_members.router):
        app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = fixture_user

    @app.middleware("http")
    async def observe_real_requests(request, call_next):
        app.state.commands[f"{request.method} {request.url.path}"] += 1
        with workspace_root_override(app.state.workspace):
            return await call_next(request)

    @app.get("/fixture/health")
    async def health():
        return {"ready": True, "fixturePid": os.getpid(), "workspaceId": WORKSPACE_ID,
                "commandCounters": dict(app.state.commands)}

    @app.get("/fixture/missions/{mission_id}")
    async def snapshot(mission_id: str, user: Annotated[dict, Depends(fixture_user)]):
        access.authorize_workspace(user, WORKSPACE_ID)
        try:
            return await app.state.driver.snapshot(mission_id)
        except ValueError as exc:
            raise HTTPException(404, "Fixture Mission not found") from exc

    @app.post("/fixture/missions/{mission_id}/advance")
    async def advance(
        mission_id: str, body: AdvanceRequest, user: Annotated[dict, Depends(fixture_user)],
    ):
        access.authorize_workspace(user, WORKSPACE_ID)
        try:
            return await app.state.driver.advance(mission_id, body.action, body.lease_seconds)
        except ValueError as exc:
            raise HTTPException(409, "Fixture transition rejected") from exc

    return app


if __name__ == "__main__":
    uvicorn.run(build_fixture_app(), host=HOST, port=PORT, access_log=False,
                log_level="warning", timeout_graceful_shutdown=10)
