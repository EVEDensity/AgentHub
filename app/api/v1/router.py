from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import a2a_adapter, agent_catalog, missions

router = APIRouter(prefix="/api/v1")
router.include_router(a2a_adapter.router)
router.include_router(agent_catalog.router)
router.include_router(missions.router)
