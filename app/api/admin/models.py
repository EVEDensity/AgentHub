"""Model configuration management — CRUD and connectivity testing for LLM providers.

Endpoints:
  POST   /models          Create a new model configuration
  GET    /models          List all model configurations
  POST   /models/{id}/test  Test connectivity to a configured model
"""

from __future__ import annotations

import hashlib
import os
import time

from fastapi import APIRouter, Depends, HTTPException

from app.db.init_db import now
from app.db.session import afetch_all, afetch_one, aexecute_insert
from app.schemas.common import ModelConfigRequest
from app.services.auth_service import get_current_user, require_admin, write_audit
from app.services.secret_service import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/models", tags=["admin-models"])


def resolve_model_api_key(explicit: str) -> str:
    """Prefer the request-provided key; fall back to the desktop-injected key.

    The desktop shell stores a Model API Key in the OS credential store and
    injects it into the local Mission Control process as
    ``AGENTHUB_DESKTOP_MODEL_API_KEY``. Per-model keys from the request always
    win; the desktop key only fills in when the request omits one.
    """
    return explicit or os.environ.get("AGENTHUB_DESKTOP_MODEL_API_KEY", "")


# ── CREATE ────────────────────────────────────────────────────────────────


@router.post("")
async def create_model(data: ModelConfigRequest, user: dict = Depends(get_current_user)) -> dict:
    """Register a new LLM provider model configuration."""
    require_admin(user)

    api_key = resolve_model_api_key(data.apiKey)
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest() if api_key else ""

    new_id = await aexecute_insert(
        "INSERT INTO model_configs(provider, model_name, api_key, api_key_hash, base_url, is_active, created_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
        data.provider, data.modelName, encrypt_secret(api_key), api_key_hash,
        data.baseUrl, 1, now(),
    )

    audit_id = write_audit(
        user["id"], "admin", "model_config_create", "L2", "approve",
        {"provider": data.provider, "modelName": data.modelName,
         "keySource": "request" if data.apiKey else ("desktop" if api_key else "empty")},
    )
    return {"status": "success", "id": int(new_id), "auditId": audit_id}


# ── LIST ──────────────────────────────────────────────────────────────────


@router.get("")
async def list_models(user: dict = Depends(get_current_user)) -> list[dict]:
    """Return all registered model configurations."""
    require_admin(user)
    return await afetch_all(
        "SELECT id, provider, model_name AS \"modelName\", base_url AS \"baseUrl\", "
        "is_active AS \"isActive\", created_at AS \"createdAt\" "
        "FROM model_configs ORDER BY id DESC"
    )


# ── TEST ──────────────────────────────────────────────────────────────────


@router.post("/{model_id}/test")
async def test_model(model_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Probe connectivity to a specific model provider and report latency."""
    require_admin(user)

    row = await afetch_one(
        "SELECT id, provider, model_name AS \"modelName\", api_key AS \"apiKey\", base_url AS \"baseUrl\" "
        "FROM model_configs WHERE id = $1",
        model_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Model config not found")

    provider = (row["provider"] or "mock").lower()
    base_url = (row.get("baseUrl") or "").rstrip("/")
    api_key = decrypt_secret(row.get("apiKey") or "")
    model_name = row.get("modelName") or ""

    from app.services.adapter_manager import adapter_manager

    start = time.perf_counter()
    status = "success"
    message = "连接正常"

    try:
        adapter = adapter_manager.get_adapter(provider)
        message = await adapter.ping(model_name, api_key, base_url)
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
            "checkedAt": now()}
