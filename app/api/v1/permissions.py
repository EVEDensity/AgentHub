"""Authenticated permission-policy synchronization for CLI clients."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db.session import aexecute, afetch_all
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/permissions", tags=["permissions"])
CurrentUser = Annotated[dict, Depends(get_current_user)]


class PolicyRule(BaseModel):
    agent_id: str = Field(default="*")
    tool_pattern: str = Field(min_length=1, max_length=200)
    path_pattern: str = Field(default="*", max_length=500)
    behavior: str = Field(pattern="^(allow|deny|ask)$")
    priority: int = Field(default=0, ge=-1000, le=1000)
    enabled: bool = True


class PolicySyncRequest(BaseModel):
    mode: str = Field(default="replace", pattern="^(merge|replace)$")
    rules: list[PolicyRule] = Field(default_factory=list, max_length=200)


def _project(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "agentId": row.get("agent_id"),
        "toolPattern": row.get("tool_pattern"),
        "pathPattern": row.get("path_pattern"),
        "behavior": row.get("behavior"),
        "source": row.get("source"),
        "priority": row.get("priority"),
        "enabled": bool(row.get("enabled", 1)),
        "createdAt": row.get("created_at"),
    }


@router.get("/policy")
async def get_policy(user: CurrentUser) -> dict:
    """Return the authenticated user's effective rules and their origins."""
    rows = await afetch_all(
        "SELECT id, agent_id, tool_pattern, path_pattern, behavior, source, priority, enabled, created_at "
        "FROM tool_permission_rules WHERE enabled=1 AND (agent_id=$1 OR agent_id='*') "
        "ORDER BY priority DESC, id ASC",
        str(user["id"]),
    )
    return {"schemaVersion": 1, "userId": str(user["id"]), "rules": [_project(row) for row in rows]}


@router.put("/policy")
async def sync_policy(data: PolicySyncRequest, user: CurrentUser) -> dict:
    """Merge or replace user-owned rules; global rules cannot be overwritten."""
    user_id = str(user["id"])
    if data.mode == "replace":
        await aexecute("DELETE FROM tool_permission_rules WHERE agent_id=$1 AND source='user'", user_id)
    for rule in data.rules:
        if rule.agent_id not in {"*", user_id}:
            raise HTTPException(status_code=403, detail="rule agent_id is outside authenticated user scope")
        await aexecute(
            "INSERT INTO tool_permission_rules (agent_id, tool_pattern, path_pattern, behavior, source, priority, enabled, created_at) "
            "VALUES ($1,$2,$3,$4,'user',$5,$6,CURRENT_TIMESTAMP)",
            rule.agent_id, rule.tool_pattern, rule.path_pattern, rule.behavior, rule.priority, 1 if rule.enabled else 0,
        )
    return await get_policy(user)
