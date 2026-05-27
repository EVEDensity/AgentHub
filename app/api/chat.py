from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.init_db import now
from app.schemas.common import ChatTaskRequest
from app.services.auth_service import get_current_user
from app.services.agent_service import list_messages
from app.services.task_state_machine import task_state_machine

router = APIRouter(prefix="/api/chat", tags=["chat"])


class SessionCreateRequest(BaseModel):
    name: str = "新建会话"


@router.get("/sessions")
async def sessions() -> list[dict]:
    from app.db.session import dict_rows

    return dict_rows("SELECT id,name,type,active,created_at AS createdAt,is_pinned AS isPinned,last_message_at AS lastMessageAt FROM sessions ORDER BY is_pinned DESC, CASE WHEN last_message_at != '' THEN last_message_at ELSE created_at END DESC")


@router.post("/sessions")
async def create_session(data: SessionCreateRequest, user: dict = Depends(get_current_user)) -> dict:
    from app.db.session import get_connection

    session_id = f"session-{uuid.uuid4().hex[:8]}"
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sessions(id,name,type,participants,active,created_at) VALUES(?,?,?,?,?,?)",
            (session_id, data.name.strip() or "新建会话", "group", "[]", 1, now()),
        )
    return {"id": session_id, "name": data.name.strip() or "新建会话", "createdAt": now(), "active": 1, "type": "group"}


@router.get("/sessions/{session_id}/messages")
async def messages(session_id: str) -> list[dict]:
    return list_messages(session_id)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    from app.db.session import get_connection

    with get_connection() as conn:
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM tasks WHERE session_id=?", (session_id,))
        cursor = conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "success", "sessionId": session_id}


@router.put("/sessions/{session_id}")
async def rename_session(session_id: str, data: dict, user: dict = Depends(get_current_user)) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    from app.db.session import get_connection

    with get_connection() as conn:
        cursor = conn.execute("UPDATE sessions SET name=? WHERE id=?", (name, session_id))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "success", "sessionId": session_id, "name": name}


@router.put("/sessions/{session_id}/pin")
async def toggle_pin_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    from app.db.session import get_connection, one_row

    row = one_row("SELECT is_pinned FROM sessions WHERE id=?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    new_val = 0 if row.get("is_pinned") else 1
    with get_connection() as conn:
        conn.execute("UPDATE sessions SET is_pinned=? WHERE id=?", (new_val, session_id))
    return {"status": "success", "sessionId": session_id, "isPinned": new_val}


@router.post("/tasks")
async def create_task(data: ChatTaskRequest, user: dict = Depends(get_current_user)) -> dict:
    if not data.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    return task_state_machine.create_task(data.sessionId, data.message)


@router.get("/workflows")
async def list_workflows() -> list[dict]:
    from app.db.session import dict_rows

    rows = dict_rows(
        "SELECT id,name,description,trigger_keywords FROM agent_routes WHERE active=1 ORDER BY is_default DESC, updated_at DESC"
    )
    for r in rows:
        import json

        r["triggerKeywords"] = json.loads(r.pop("trigger_keywords", "[]") or "[]")
        r["routeId"] = r.pop("id")
    return rows
