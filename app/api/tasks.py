from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services.task_state_machine import task_state_machine
from app.services.template_engine import template_engine

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(sessionId: str | None = None) -> list[dict]:
    return await task_state_machine.list_tasks(sessionId)


@router.get("/{task_id}/status")
async def status(task_id: str) -> dict:
    task = await task_state_machine.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": task["status"], "dagProgress": task["dagProgress"]}


@router.get("/templates/list")
async def templates() -> list[dict]:
    return await template_engine.list_templates()
