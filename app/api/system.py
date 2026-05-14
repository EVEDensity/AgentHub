from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["preview"])


@router.get("/preview/{task_id}")
async def preview(task_id: str) -> dict[str, str]:
    return {"taskId": task_id, "url": "http://localhost:3000"}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "AgentHub"}
