from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.db.session import dict_rows, get_connection
from app.schemas.common import AgentCreateRequest
from app.services.auth_service import get_current_user, require_admin, write_audit

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/registry")
async def registry() -> list[dict]:
    return dict_rows("SELECT agent_id AS agentId,domain,status,adapter_type AS adapterType,risk_level AS riskLevel FROM agent_registry ORDER BY agent_id")


@router.post("/registry")
async def create_agent(data: AgentCreateRequest, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    if not data.agentId.strip():
        raise HTTPException(status_code=400, detail="Agent ID 不能为空")
    with get_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO agent_registry(agent_id,domain,status,adapter_type,risk_level) VALUES(?,?,?,?,?)",
                (data.agentId.strip(), data.domain.strip(), "sleeping", data.adapterType, data.riskLevel),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Agent 已存在或参数无效") from exc
    audit_id = write_audit(user["id"], data.agentId, "agent_create", "L2", "approve", data.model_dump())
    return {"status": "success", "agentId": data.agentId, "auditId": audit_id}
