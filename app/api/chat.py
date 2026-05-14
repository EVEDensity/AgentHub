from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.common import ChatTaskRequest
from app.services.auth_service import get_current_user
from app.services.agent_service import list_messages
from app.services.task_state_machine import task_state_machine

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/sessions")
async def sessions() -> list[dict]:
    from app.db.session import dict_rows

    return dict_rows("SELECT id,name,type,active,created_at AS createdAt FROM sessions ORDER BY created_at")


@router.get("/sessions/{session_id}/messages")
async def messages(session_id: str) -> list[dict]:
    return list_messages(session_id)


@router.post("/tasks")
async def create_task(data: ChatTaskRequest, user: dict = Depends(get_current_user)) -> dict:
    if not data.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    return task_state_machine.create_task(data.sessionId, data.message)
