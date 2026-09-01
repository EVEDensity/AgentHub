from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api import skills
from app.api.v1 import (
    a2a_adapter,
    access,
    agent_catalog,
    chat_mission,
    missions,
    workspace_members,
)
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/v1")
router.include_router(a2a_adapter.router)
router.include_router(agent_catalog.router)
router.include_router(missions.router)
router.include_router(access.router)
router.include_router(workspace_members.router)
router.include_router(chat_mission.router)
# Versioned skills mount: same implementation as the legacy /api/skills
# surface, but authenticated (I-7a migration slice).
router.include_router(skills.router, dependencies=[Depends(get_current_user)])
