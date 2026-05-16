from __future__ import annotations

import hashlib
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.db.init_db import now
from app.db.session import dict_rows, get_connection, one_row
from app.schemas.common import AgentRouteActiveRequest, AgentRouteRequest, AuditConfirmRequest, ModelConfigRequest, RoleBindRequest
from app.services.agent_route_service import agent_route_service
from app.services.auth_service import get_current_user, require_admin, write_audit
from app.services.secret_service import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/model-config")
async def save_model_config(data: ModelConfigRequest, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    api_key_hash = hashlib.sha256(data.apiKey.encode()).hexdigest() if data.apiKey else ""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO model_configs(provider,model_name,api_key,api_key_hash,base_url,is_active,created_at) VALUES(?,?,?,?,?,?,?)",
            (data.provider, data.modelName, encrypt_secret(data.apiKey), api_key_hash, data.baseUrl, 1, now()),
        )
    audit_id = write_audit(user["id"], "admin", "model_config_create", "L2", "approve", {"provider": data.provider, "modelName": data.modelName})
    return {"status": "success", "id": cursor.lastrowid, "auditId": audit_id}


@router.get("/model-config")
async def list_model_configs(user: dict = Depends(get_current_user)) -> list[dict]:
    require_admin(user)
    return dict_rows("SELECT id,provider,model_name AS modelName,base_url AS baseUrl,is_active AS isActive,created_at AS createdAt FROM model_configs ORDER BY id DESC")


@router.post("/model-config/{model_id}/test")
async def test_model_config(model_id: int, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    row = one_row("SELECT id,provider,model_name AS modelName,api_key AS apiKey,base_url AS baseUrl FROM model_configs WHERE id=?", (model_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Model config not found")

    provider = (row["provider"] or "mock").lower()
    base_url = (row.get("baseUrl") or "").rstrip("/")
    api_key = decrypt_secret(row.get("apiKey") or "")
    start = time.perf_counter()
    status = "success"
    message = "连接正常"
    endpoint = "mock://local"

    openai_compatible = {
        "openai": "https://api.openai.com/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "minimax": "https://api.minimax.chat/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "doubao": "https://ark.cn-beijing.volces.com/api/v3",
        "custom_openai": "",
    }

    try:
        if provider == "mock":
            message = "Mock 模型可用，本地无需网络检测"
        elif provider == "ollama":
            endpoint = (base_url or "http://localhost:11434") + "/api/tags"
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(endpoint)
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail=response.text[:300])
            message = f"Ollama 已连接，可用模型 {len(response.json().get('models', []))} 个"
        elif provider in openai_compatible:
            endpoint = (base_url or openai_compatible[provider]) + "/models"
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(endpoint, headers=headers)
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail=response.text[:300])
            message = f"{provider} OpenAI 兼容接口连接正常"
        elif provider == "anthropic":
            endpoint = (base_url or "https://api.anthropic.com") + "/v1/models"
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"} if api_key else {}
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(endpoint, headers=headers)
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail=response.text[:300])
            message = "Anthropic 接口连接正常"
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    except HTTPException as exc:
        status = "failed"
        message = str(exc.detail)
    except Exception as exc:
        status = "failed"
        message = str(exc)

    latency_ms = int((time.perf_counter() - start) * 1000)
    write_audit(user["id"], "admin", "model_config_test", "L1", status, {"modelConfigId": model_id, "provider": provider, "latencyMs": latency_ms, "message": message})
    return {"status": status, "message": message, "latencyMs": latency_ms, "endpoint": endpoint, "checkedAt": now()}


@router.post("/role-bind")
async def role_bind(data: RoleBindRequest, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    with get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO role_bindings(role,model_config_id,prompt,updated_at) VALUES(?,?,?,?)", (data.role, data.modelConfigId, data.prompt, now()))
    audit_id = write_audit(user["id"], data.role, "role_bind", "L2", "approve", data.model_dump())
    return {"status": "success", "auditId": audit_id}


@router.get("/role-bind")
async def list_role_bindings(user: dict = Depends(get_current_user)) -> list[dict]:
    require_admin(user)
    return dict_rows("SELECT role,model_config_id AS modelConfigId,prompt,updated_at AS updatedAt FROM role_bindings ORDER BY role")


@router.get("/agent-routes")
async def list_agent_routes(user: dict = Depends(get_current_user)) -> list[dict]:
    require_admin(user)
    return agent_route_service.list_routes()


@router.post("/agent-routes")
async def create_agent_route(data: AgentRouteRequest, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    try:
        route = agent_route_service.create_route(data.name, data.description, data.triggerKeywords, data.nodes, data.isDefault)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_id = write_audit(user["id"], "admin", "agent_route_create", "L2", "approve", {"routeId": route["id"], "name": route["name"]})
    return {"status": "success", "route": route, "auditId": audit_id}


@router.post("/agent-routes/{route_id}/default")
async def set_default_agent_route(route_id: int, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    try:
        route = agent_route_service.set_default(route_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit_id = write_audit(user["id"], "admin", "agent_route_set_default", "L2", "approve", {"routeId": route_id})
    return {"status": "success", "route": route, "auditId": audit_id}


@router.patch("/agent-routes/{route_id}/active")
async def set_agent_route_active(route_id: int, data: AgentRouteActiveRequest, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    try:
        route = agent_route_service.set_active(route_id, data.active)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit_id = write_audit(user["id"], "admin", "agent_route_active", "L1", "approve", {"routeId": route_id, "active": data.active})
    return {"status": "success", "route": route, "auditId": audit_id}


@router.get("/users")
async def users(user: dict = Depends(get_current_user)) -> list[dict]:
    require_admin(user)
    return dict_rows("SELECT id,name,role,created_at AS createdAt FROM users ORDER BY created_at")


@router.get("/audit-log")
async def audit_logs(user: dict = Depends(get_current_user)) -> list[dict]:
    require_admin(user)
    return dict_rows("SELECT id,user_id AS userId,agent_id AS agentId,action,risk_level AS riskLevel,decision,content_hash AS contentHash,payload_json AS payload,timestamp FROM audit_log ORDER BY timestamp DESC LIMIT 200")


@router.post("/audit/confirm")
async def audit_confirm(data: AuditConfirmRequest, user: dict = Depends(get_current_user)) -> dict:
    audit_id = write_audit(user["id"], data.agentId, data.action, data.riskLevel, data.decision, data.payload)
    return {"status": "success", "auditId": audit_id}
