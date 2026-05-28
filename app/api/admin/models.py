"""Model configuration management — CRUD and connectivity testing for LLM providers.

Endpoints:
  POST   /models          Create a new model configuration
  GET    /models          List all model configurations
  POST   /models/{id}/test  Test connectivity to a configured model
"""

from __future__ import annotations

import hashlib
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.db.init_db import now
from app.db.session import dict_rows, get_connection, one_row
from app.schemas.common import ModelConfigRequest
from app.services.auth_service import get_current_user, require_admin, write_audit
from app.services.secret_service import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/models", tags=["admin-models"])

# ---------------------------------------------------------------------------
# Provider → default base URL mapping for connectivity tests
# ---------------------------------------------------------------------------
_OPENAI_COMPATIBLE: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "minimax": "https://api.minimax.chat/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "doubao": "https://ark.cn-beijing.volces.com/api/v3",
    "custom_openai": "",
}


# ── CREATE ────────────────────────────────────────────────────────────────


@router.post("")
async def create_model(data: ModelConfigRequest, user: dict = Depends(get_current_user)) -> dict:
    """Register a new LLM provider model configuration."""
    require_admin(user)

    api_key_hash = hashlib.sha256(data.apiKey.encode()).hexdigest() if data.apiKey else ""

    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO model_configs(provider, model_name, api_key, api_key_hash, base_url, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (data.provider, data.modelName, encrypt_secret(data.apiKey), api_key_hash,
             data.baseUrl, 1, now()),
        )

    audit_id = write_audit(
        user["id"], "admin", "model_config_create", "L2", "approve",
        {"provider": data.provider, "modelName": data.modelName},
    )
    return {"status": "success", "id": cursor.lastrowid, "auditId": audit_id}


# ── LIST ──────────────────────────────────────────────────────────────────


@router.get("")
async def list_models(user: dict = Depends(get_current_user)) -> list[dict]:
    """Return all registered model configurations."""
    require_admin(user)
    return dict_rows(
        "SELECT id, provider, model_name AS modelName, base_url AS baseUrl, "
        "is_active AS isActive, created_at AS createdAt "
        "FROM model_configs ORDER BY id DESC"
    )


# ── TEST ──────────────────────────────────────────────────────────────────


@router.post("/{model_id}/test")
async def test_model(model_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Probe connectivity to a specific model provider and report latency."""
    require_admin(user)

    row = one_row(
        "SELECT id, provider, model_name AS modelName, api_key AS apiKey, base_url AS baseUrl "
        "FROM model_configs WHERE id = ?",
        (model_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Model config not found")

    provider = (row["provider"] or "mock").lower()
    base_url = (row.get("baseUrl") or "").rstrip("/")
    api_key = decrypt_secret(row.get("apiKey") or "")

    start = time.perf_counter()
    status = "success"
    message = "连接正常"
    endpoint = "mock://local"

    try:
        if provider == "mock":
            message = "Mock 模型可用，本地无需网络检测"
        elif provider == "ollama":
            endpoint = (base_url or "http://localhost:11434") + "/api/tags"
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(endpoint)
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail=resp.text[:300])
            model_count = len(resp.json().get("models", []))
            message = f"Ollama 已连接，可用模型 {model_count} 个"
        elif provider in _OPENAI_COMPATIBLE:
            endpoint = (base_url or _OPENAI_COMPATIBLE[provider]) + "/models"
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(endpoint, headers=headers)
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail=resp.text[:300])
            message = f"{provider} OpenAI 兼容接口连接正常"
        elif provider == "anthropic":
            endpoint = (base_url or "https://api.anthropic.com") + "/v1/models"
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"} if api_key else {}
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(endpoint, headers=headers)
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail=resp.text[:300])
            message = "Anthropic 接口连接正常"
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    except HTTPException:
        status = "failed"
        raise
    except Exception as exc:
        status = "failed"
        message = str(exc)
    finally:
        latency_ms = int((time.perf_counter() - start) * 1000)
        write_audit(
            user["id"], "admin", "model_config_test", "L1", status,
            {"modelConfigId": model_id, "provider": provider, "latencyMs": latency_ms, "message": message},
        )

    return {"status": status, "message": message, "latencyMs": latency_ms,
            "endpoint": endpoint, "checkedAt": now()}
