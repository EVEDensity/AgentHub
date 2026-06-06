from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.config import DATA_DIR
from app.db.session import afetch_all, afetch_one, aexecute
from app.schemas.common import AgentCreateRequest, AgentUpdateRequest
from app.services.adapter_manager import adapter_manager
from app.services.auth_service import get_current_user, require_admin, write_audit
from app.services.secret_service import decrypt_secret, encrypt_secret
from app.utils.async_file import aexists, aread_bytes, awrite_bytes

router = APIRouter(prefix="/api/agent", tags=["agent"])

AVATAR_DIR = DATA_DIR / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/registry")
async def registry() -> list[dict]:
    rows = await afetch_all(
        "SELECT agent_id AS \"agentId\",domain,status,adapter_type AS \"adapterType\","
        "base_model_name AS \"baseModelName\",risk_level AS \"rankLevel\","
        "duty_note AS \"dutyNote\",display_name AS \"displayName\","
        "avatar_url AS \"avatarUrl\",capability_tags AS \"capabilityTags\","
        "base_url AS \"baseUrl\" FROM agent_registry ORDER BY agent_id"
    )
    for row in rows:
        try:
            row["capabilityTags"] = json.loads(row.get("capabilityTags", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            row["capabilityTags"] = []
    return rows


@router.post("/registry")
async def create_agent(data: AgentCreateRequest, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    if not data.agentId.strip():
        raise HTTPException(status_code=400, detail="Agent ID 不能为空")
    try:
        await aexecute(
            "INSERT INTO agent_registry(agent_id,domain,status,adapter_type,base_model_name,"
            "risk_level,duty_note,display_name,avatar_url,capability_tags,base_url,api_key) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
            data.agentId.strip(),
            data.domain.strip(),
            "sleeping",
            data.adapterType,
            data.baseModelName.strip(),
            data.rankLevel,
            data.dutyNote.strip(),
            data.displayName.strip(),
            data.avatarUrl.strip(),
            json.dumps(data.capabilityTags or [], ensure_ascii=False),
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

    tags_json = json.dumps(data.capabilityTags or [], ensure_ascii=False)
    if data.apiKey.strip():
        await aexecute(
            "UPDATE agent_registry SET domain=$1,adapter_type=$2,base_model_name=$3,"
            "risk_level=$4,duty_note=$5,display_name=$6,avatar_url=$7,"
            "capability_tags=$8,base_url=$9,api_key=$10 WHERE agent_id=$11",
            data.domain.strip(), data.adapterType, data.baseModelName.strip(),
            data.rankLevel, data.dutyNote.strip(), data.displayName.strip(),
            data.avatarUrl.strip(), tags_json, data.baseUrl.strip(),
            encrypt_secret(data.apiKey.strip()), agent_id,
        )
    else:
        await aexecute(
            "UPDATE agent_registry SET domain=$1,adapter_type=$2,base_model_name=$3,"
            "risk_level=$4,duty_note=$5,display_name=$6,avatar_url=$7,"
            "capability_tags=$8,base_url=$9 WHERE agent_id=$10",
            data.domain.strip(), data.adapterType, data.baseModelName.strip(),
            data.rankLevel, data.dutyNote.strip(), data.displayName.strip(),
            data.avatarUrl.strip(), tags_json, data.baseUrl.strip(), agent_id,
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


# ── Avatar upload / serve ─────────────────────────────────────────

@router.post("/registry/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
) -> dict:
    """Upload an agent avatar image, return the URL path."""
    require_admin(user)
    safe_name = Path(file.filename or "avatar.png").name
    if ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    ext = Path(safe_name).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}:
        raise HTTPException(status_code=400, detail="Unsupported image format")
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:  # 2 MB limit
        raise HTTPException(status_code=413, detail="Avatar too large (max 2 MB)")
    avatar_id = uuid.uuid4().hex[:12]
    avatar_filename = f"avatar_{avatar_id}{ext}"
    avatar_path = AVATAR_DIR / avatar_filename
    await awrite_bytes(avatar_path, content)
    return {
        "avatarUrl": f"/api/agent/registry/avatar/{avatar_filename}",
        "filename": avatar_filename,
    }


@router.get("/registry/avatar/{filename}")
async def get_avatar(filename: str):
    """Serve an uploaded avatar image."""
    safe_name = Path(filename).name
    if ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    avatar_path = AVATAR_DIR / safe_name
    if not await aexists(avatar_path):
        raise HTTPException(status_code=404, detail="Avatar not found")
    ext = safe_name.split(".")[-1].lower() if "." in safe_name else "png"
    content_type = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
        "bmp": "image/bmp", "ico": "image/x-icon",
    }.get(ext, "image/png")
    from fastapi.responses import Response
    content = await aread_bytes(avatar_path)
    return Response(content=content, media_type=content_type)
