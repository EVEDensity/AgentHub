from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import missions

router = APIRouter(prefix="/api/v1")
router.include_router(missions.router)
