from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.db.session import dict_rows, get_connection, one_row
from app.schemas.common import AgentCreateRequest, AgentUpdateRequest
from app.services.adapter_manager import adapter_manager
from app.services.auth_service import get_current_user, require_admin, write_audit
from app.services.secret_service import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/registry")
async def registry() -> list[dict]:
    return dict_rows(
        "SELECT agent_id AS agentId,domain,status,adapter_type AS adapterType,base_model_name AS baseModelName,risk_level AS rankLevel,duty_note AS dutyNote,base_url AS baseUrl FROM agent_registry ORDER BY agent_id"
    )


@router.post("/registry")
async def create_agent(data: AgentCreateRequest, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    if not data.agentId.strip():
        raise HTTPException(status_code=400, detail="Agent ID 不能为空")
    with get_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO agent_registry(agent_id,domain,status,adapter_type,base_model_name,risk_level,duty_note,base_url,api_key) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    data.agentId.strip(),
                    data.domain.strip(),
                    "sleeping",
                    data.adapterType,
                    data.baseModelName.strip(),
                    data.rankLevel,
                    data.dutyNote.strip(),
                    data.baseUrl.strip(),
                    encrypt_secret(data.apiKey.strip()),
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Agent 已存在或参数无效") from exc
    audit_id = write_audit(user["id"], data.agentId, "agent_create", "L2", "approve", {**data.model_dump(), "apiKey": "***" if data.apiKey else ""})
    return {"status": "success", "agentId": data.agentId, "auditId": audit_id}


@router.delete("/registry/{agent_id}")
async def delete_agent(agent_id: str, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    with get_connection() as conn:
        deleted = conn.execute("DELETE FROM agent_registry WHERE agent_id=?", (agent_id,)).rowcount
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    audit_id = write_audit(user["id"], agent_id, "agent_delete", "L2", "approve", {"agentId": agent_id})
    return {"status": "success", "agentId": agent_id, "auditId": audit_id}


@router.put("/registry/{agent_id}")
async def update_agent(agent_id: str, data: AgentUpdateRequest, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    if agent_id != data.agentId:
        raise HTTPException(status_code=400, detail="路径和请求中的 Agent ID 不一致")

    with get_connection() as conn:
        exists = conn.execute("SELECT agent_id FROM agent_registry WHERE agent_id=?", (agent_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Agent 不存在")

        if data.apiKey.strip():
            conn.execute(
                "UPDATE agent_registry SET domain=?,adapter_type=?,base_model_name=?,risk_level=?,duty_note=?,base_url=?,api_key=? WHERE agent_id=?",
                (data.domain.strip(), data.adapterType, data.baseModelName.strip(), data.rankLevel, data.dutyNote.strip(), data.baseUrl.strip(), encrypt_secret(data.apiKey.strip()), agent_id),
            )
        else:
            conn.execute(
                "UPDATE agent_registry SET domain=?,adapter_type=?,base_model_name=?,risk_level=?,duty_note=?,base_url=? WHERE agent_id=?",
                (data.domain.strip(), data.adapterType, data.baseModelName.strip(), data.rankLevel, data.dutyNote.strip(), data.baseUrl.strip(), agent_id),
            )

    audit_id = write_audit(user["id"], agent_id, "agent_update", "L2", "approve", {**data.model_dump(), "apiKey": "***" if data.apiKey else ""})
    return {"status": "success", "agentId": agent_id, "auditId": audit_id}


@router.post("/registry/{agent_id}/test")
async def test_agent_model(agent_id: str, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    row = one_row("SELECT agent_id AS agentId,adapter_type AS adapterType,base_url AS baseUrl,api_key AS apiKey FROM agent_registry WHERE agent_id=?", (agent_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    adapter_type = (row.get("adapterType") or "mock").lower()
    base_url = row.get("baseUrl") or ""
    api_key = decrypt_secret(row.get("apiKey") or "")

    adapter = adapter_manager.get_adapter(adapter_type)
    try:
        result = await adapter.execute_prompt("ping", "ping", api_key, base_url)
        ok = bool(result)
        message = "连接正常" if ok else "未返回有效响应"
    except Exception as exc:
        ok = False
        message = str(exc)

    with get_connection() as conn:
        conn.execute("UPDATE agent_registry SET status=? WHERE agent_id=?", ("online" if ok else "offline", agent_id))

    write_audit(user["id"], agent_id, "agent_test_connection", "L1", "approve" if ok else "reject", {"adapterType": adapter_type, "baseUrl": base_url, "message": message})
    return {"status": "success" if ok else "failed", "agentId": agent_id, "message": message}
