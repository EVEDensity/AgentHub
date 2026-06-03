from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.db.session import afetch_all, afetch_one, aexecute
from app.schemas.common import AgentCreateRequest, AgentUpdateRequest
from app.services.adapter_manager import adapter_manager
from app.services.auth_service import get_current_user, require_admin, write_audit
from app.services.secret_service import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/registry")
async def registry() -> list[dict]:
    return await afetch_all(
        "SELECT agent_id AS \"agentId\",domain,status,adapter_type AS \"adapterType\",base_model_name AS \"baseModelName\",risk_level AS \"rankLevel\",duty_note AS \"dutyNote\",base_url AS \"baseUrl\" FROM agent_registry ORDER BY agent_id"
    )


@router.post("/registry")
async def create_agent(data: AgentCreateRequest, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    if not data.agentId.strip():
        raise HTTPException(status_code=400, detail="Agent ID 不能为空")
    try:
        await aexecute(
            "INSERT INTO agent_registry(agent_id,domain,status,adapter_type,base_model_name,risk_level,duty_note,base_url,api_key) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            data.agentId.strip(),
            data.domain.strip(),
            "sleeping",
            data.adapterType,
            data.baseModelName.strip(),
            data.rankLevel,
            data.dutyNote.strip(),
            data.baseUrl.strip(),
            encrypt_secret(data.apiKey.strip()),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Agent 已存在或参数无效") from exc
    audit_id = write_audit(user["id"], data.agentId, "agent_create", "L2", "approve", {**data.model_dump(), "apiKey": "***" if data.apiKey else ""})
    return {"status": "success", "agentId": data.agentId, "auditId": audit_id}


@router.delete("/registry/{agent_id}")
async def delete_agent(agent_id: str, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    deleted = await afetch_one(
        "DELETE FROM agent_registry WHERE agent_id=$1 RETURNING agent_id", agent_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    audit_id = write_audit(user["id"], agent_id, "agent_delete", "L2", "approve", {"agentId": agent_id})
    return {"status": "success", "agentId": agent_id, "auditId": audit_id}


@router.put("/registry/{agent_id}")
async def update_agent(agent_id: str, data: AgentUpdateRequest, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    if agent_id != data.agentId:
        raise HTTPException(status_code=400, detail="路径和请求中的 Agent ID 不一致")

    exists = await afetch_one("SELECT agent_id FROM agent_registry WHERE agent_id=$1", agent_id)
    if not exists:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    if data.apiKey.strip():
        await aexecute(
            "UPDATE agent_registry SET domain=$1,adapter_type=$2,base_model_name=$3,risk_level=$4,duty_note=$5,base_url=$6,api_key=$7 WHERE agent_id=$8",
            data.domain.strip(), data.adapterType, data.baseModelName.strip(), data.rankLevel, data.dutyNote.strip(), data.baseUrl.strip(), encrypt_secret(data.apiKey.strip()), agent_id,
        )
    else:
        await aexecute(
            "UPDATE agent_registry SET domain=$1,adapter_type=$2,base_model_name=$3,risk_level=$4,duty_note=$5,base_url=$6 WHERE agent_id=$7",
            data.domain.strip(), data.adapterType, data.baseModelName.strip(), data.rankLevel, data.dutyNote.strip(), data.baseUrl.strip(), agent_id,
        )

    audit_id = write_audit(user["id"], agent_id, "agent_update", "L2", "approve", {**data.model_dump(), "apiKey": "***" if data.apiKey else ""})
    return {"status": "success", "agentId": agent_id, "auditId": audit_id}


@router.post("/registry/{agent_id}/test")
async def test_agent_model(agent_id: str, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    row = await afetch_one("SELECT agent_id AS \"agentId\",adapter_type AS \"adapterType\",base_model_name AS \"baseModelName\",base_url AS \"baseUrl\",api_key AS \"apiKey\" FROM agent_registry WHERE agent_id=$1", agent_id)
    if not row:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    adapter_type = (row.get("adapterType") or "mock").lower()
    base_url = row.get("baseUrl") or ""
    base_model_name = row.get("baseModelName") or ""
    api_key = decrypt_secret(row.get("apiKey") or "")

    adapter = adapter_manager.get_adapter(adapter_type)
    try:
        # Use the agent's configured model; adapter falls back to its default when empty
        test_model = base_model_name or adapter.default_model
        result = await adapter.execute_prompt("ping", test_model, api_key, base_url)
        ok = bool(result)
        message = "连接正常" if ok else "未返回有效响应"
    except Exception as exc:
        ok = False
        message = str(exc)

    await aexecute("UPDATE agent_registry SET status=$1 WHERE agent_id=$2", "online" if ok else "offline", agent_id)

    write_audit(user["id"], agent_id, "agent_test_connection", "L1", "approve" if ok else "reject", {"adapterType": adapter_type, "baseUrl": base_url, "message": message})
    return {"status": "success" if ok else "failed", "agentId": agent_id, "message": message}
