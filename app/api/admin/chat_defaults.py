"""Default chat agent selection — controls which agent handles un-directed chat.

Endpoints:
  GET    /chat-defaults   Return the current default chat agent
  POST   /chat-defaults   Set the default chat agent
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.db.init_db import now
from app.db.session import afetch_one, aexecute
from app.schemas.common import DefaultChatAgentRequest
from app.services.agent_service import lookup_agent
from app.services.auth_service import get_current_user, require_admin, write_audit

router = APIRouter(prefix="/chat-defaults", tags=["admin-chat-defaults"])


@router.get("")
async def get_default(user: dict = Depends(get_current_user)) -> dict:
    """Return the agentId of the current default chat agent."""
    require_admin(user)
    row = await afetch_one("SELECT value FROM system_config WHERE key = 'default_chat_agent'")
    return {"agentId": row["value"] if row else "Orchestrator"}


@router.post("")
async def set_default(data: DefaultChatAgentRequest, user: dict = Depends(get_current_user)) -> dict:
    """Promote an agent to be the default for un-directed chat messages."""
    require_admin(user)

    agent = await lookup_agent(data.agentId, user["id"], columns="agent_id")
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {data.agentId}")

    await aexecute(
        "INSERT INTO system_config(key, value, updated_at) VALUES ('default_chat_agent', $1, $2) "
        "ON CONFLICT(key) DO UPDATE SET value=$1, updated_at=$2",
        data.agentId, now(),
    )

    write_audit(
        user["id"], data.agentId, "set_default_chat_agent", "L2", "approve",
        {"agentId": data.agentId},
    )
    return {"status": "success", "agentId": data.agentId}
