from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

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

AVATAR_MAX_BYTES = 2 * 1024 * 1024  # 2 MB

_MIME_BY_EXT = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
    "bmp": "image/bmp", "ico": "image/x-icon",
}

_ALLOWED_EXTENSIONS = set(_MIME_BY_EXT.keys())


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _avatar_url_for(agent_id: str) -> str:
    """Return the canonical DB-backed avatar URL for an agent."""
    return f"/api/agent/registry/avatar/{agent_id}"


def _content_type_for(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    return _MIME_BY_EXT.get(ext, "image/png")


async def _migrate_avatar_to_db(agent_id: str, avatar_url: str) -> bool:
    """Try to read an avatar from filesystem (legacy URL) → store in DB.

    Returns True if migration succeeded, False otherwise.
    """
    if not avatar_url or "/registry/avatar/" not in avatar_url:
        return False

    # Extract filename from legacy URL like /api/agent/registry/avatar/filename.png
    filename = avatar_url.rsplit("/", 1)[-1]
    safe_name = Path(filename).name
    if safe_name != filename or ".." in filename:
        return False

    file_path = AVATAR_DIR / safe_name
    if not await aexists(file_path):
        return False

    try:
        content = await aread_bytes(file_path)
        mime = _content_type_for(safe_name)
        await aexecute(
            "UPDATE agent_registry SET avatar_data=$1, avatar_mime=$2 WHERE agent_id=$3",
            content, mime, agent_id,
        )
        return True
    except Exception:
        return False


def _validate_image(content: bytes, filename: str) -> None:
    """Raise HTTPException if image is invalid or too large."""
    safe_name = Path(filename).name
    if ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    ext = Path(safe_name).suffix.lower().lstrip(".")
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported image format")
    if len(content) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Avatar too large (max 2 MB)")


# ═══════════════════════════════════════════════════════════════════════
# Agent Registry CRUD
# ═══════════════════════════════════════════════════════════════════════


@router.get("/registry")
async def registry() -> list[dict]:
    rows = await afetch_all(
        "SELECT agent_id AS \"agentId\",domain,status,adapter_type AS \"adapterType\","
        "base_model_name AS \"baseModelName\",risk_level AS \"rankLevel\","
        "duty_note AS \"dutyNote\",display_name AS \"displayName\","
        "CASE WHEN avatar_data IS NOT NULL AND avatar_mime != '' "
        "  THEN '/api/agent/registry/avatar/' || agent_id "
        "  ELSE avatar_url "
        "END AS \"avatarUrl\","
        "capability_tags AS \"capabilityTags\","
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

    agent_id = data.agentId.strip()

    # ── Handle avatar: migrate from filesystem URL → DB storage ─────
    avatar_url = data.avatarUrl.strip()
    if avatar_url and "/registry/avatar/" in avatar_url:
        # Try to migrate existing filesystem avatar to DB on create
        migrated = await _migrate_avatar_to_db(agent_id, avatar_url)
        if migrated:
            avatar_url = _avatar_url_for(agent_id)

    try:
        await aexecute(
            "INSERT INTO agent_registry(agent_id,domain,status,adapter_type,base_model_name,"
            "risk_level,duty_note,display_name,avatar_url,capability_tags,base_url,api_key) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
            agent_id,
            data.domain.strip(),
            "sleeping",
            data.adapterType,
            data.baseModelName.strip(),
            data.rankLevel,
            data.dutyNote.strip(),
            data.displayName.strip(),
            avatar_url,
            json.dumps(data.capabilityTags or [], ensure_ascii=False),
            data.baseUrl.strip(),
            encrypt_secret(data.apiKey.strip()),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Agent 已存在或参数无效") from exc
    audit_id = write_audit(user["id"], agent_id, "agent_create", "L2", "approve", {**data.model_dump(), "apiKey": "***" if data.apiKey else ""})
    return {"status": "success", "agentId": agent_id, "auditId": audit_id}


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

    # ── Handle avatar: migrate from filesystem URL → DB storage ─────
    avatar_url = data.avatarUrl.strip()
    if avatar_url and "/registry/avatar/" in avatar_url:
        migrated = await _migrate_avatar_to_db(agent_id, avatar_url)
        if migrated:
            avatar_url = _avatar_url_for(agent_id)

    tags_json = json.dumps(data.capabilityTags or [], ensure_ascii=False)
    if data.apiKey.strip():
        await aexecute(
            "UPDATE agent_registry SET domain=$1,adapter_type=$2,base_model_name=$3,"
            "risk_level=$4,duty_note=$5,display_name=$6,avatar_url=$7,"
            "capability_tags=$8,base_url=$9,api_key=$10 WHERE agent_id=$11",
            data.domain.strip(), data.adapterType, data.baseModelName.strip(),
            data.rankLevel, data.dutyNote.strip(), data.displayName.strip(),
            avatar_url, tags_json, data.baseUrl.strip(),
            encrypt_secret(data.apiKey.strip()), agent_id,
        )
    else:
        await aexecute(
            "UPDATE agent_registry SET domain=$1,adapter_type=$2,base_model_name=$3,"
            "risk_level=$4,duty_note=$5,display_name=$6,avatar_url=$7,"
            "capability_tags=$8,base_url=$9 WHERE agent_id=$10",
            data.domain.strip(), data.adapterType, data.baseModelName.strip(),
            data.rankLevel, data.dutyNote.strip(), data.displayName.strip(),
            avatar_url, tags_json, data.baseUrl.strip(), agent_id,
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
        test_model = base_model_name or getattr(adapter, "default_model", "")
        message = await adapter.ping(test_model, api_key, base_url)
        ok = True
    except Exception as exc:
        ok = False
        message = str(exc)

    await aexecute("UPDATE agent_registry SET status=$1 WHERE agent_id=$2", "online" if ok else "offline", agent_id)

    write_audit(user["id"], agent_id, "agent_test_connection", "L1", "approve" if ok else "reject", {"adapterType": adapter_type, "baseUrl": base_url, "message": message})
    return {"status": "success" if ok else "failed", "agentId": agent_id, "message": message}


# ═══════════════════════════════════════════════════════════════════════
# Avatar Upload / Serve (DB-backed with filesystem fallback)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/registry/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    agentId: str = Form(""),
    user: dict = Depends(get_current_user),
) -> dict:
    """Upload an agent avatar image.

    - If **agentId** is provided and the agent exists, the image is stored
      directly in PostgreSQL BYTEA and the DB-backup covers it automatically.
    - If no agentId is given, the file is saved to local disk (legacy mode).
    """
    require_admin(user)
    content = await file.read()
    _validate_image(content, file.filename or "avatar.png")

    ext = Path(file.filename or "avatar.png").suffix.lower()
    mime = _MIME_BY_EXT.get(ext.lstrip("."), "image/png")

    # ── DB path: store in agent_registry.avatar_data ──────────────
    if agentId.strip():
        existing = await afetch_one("SELECT agent_id FROM agent_registry WHERE agent_id=$1", agentId.strip())
        if existing:
            await aexecute(
                "UPDATE agent_registry SET avatar_data=$1, avatar_mime=$2, avatar_url=$3 WHERE agent_id=$4",
                content, mime, _avatar_url_for(agentId.strip()), agentId.strip(),
            )
            # Also save a local copy for backward compat
            try:
                avatar_path = AVATAR_DIR / f"{agentId.strip()}{ext}"
                await awrite_bytes(avatar_path, content)
            except Exception:
                pass
            return {
                "avatarUrl": _avatar_url_for(agentId.strip()),
                "filename": f"{agentId.strip()}{ext}",
                "storedInDb": True,
            }
        # Agent doesn't exist yet — fall through to filesystem

    # ── Filesystem path (legacy, no agentId or agent not found) ───
    avatar_id = uuid.uuid4().hex[:12]
    avatar_filename = f"avatar_{avatar_id}{ext}"
    avatar_path = AVATAR_DIR / avatar_filename
    await awrite_bytes(avatar_path, content)
    return {
        "avatarUrl": f"/api/agent/registry/avatar/{avatar_filename}",
        "filename": avatar_filename,
        "storedInDb": False,
    }


@router.get("/registry/avatar/{ident}")
async def get_avatar(ident: str):
    """Serve an avatar image.

    Lookup order:
    1. **agent_id** — check ``agent_registry.avatar_data`` (DB-backed).
    2. **filename** — serve from local disk (legacy fallback).
    """
    from fastapi.responses import Response

    # ── 1. Try DB lookup by agent_id ──────────────────────────────
    row = await afetch_one(
        "SELECT avatar_data, avatar_mime FROM agent_registry WHERE agent_id=$1",
        ident,
    )
    if row and row.get("avatar_data") is not None:
        content = row["avatar_data"]
        mime = row.get("avatar_mime") or "image/png"
        if isinstance(content, memoryview):
            content = bytes(content)
        return Response(content=content, media_type=mime)

    # ── 2. Filesystem fallback (legacy filename-based avatars) ──
    safe_name = Path(ident).name
    if ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    avatar_path = AVATAR_DIR / safe_name
    if not await aexists(avatar_path):
        raise HTTPException(status_code=404, detail="Avatar not found")

    content = await aread_bytes(avatar_path)
    mime = _content_type_for(safe_name)
    return Response(content=content, media_type=mime)


# ═══════════════════════════════════════════════════════════════════════
# Bulk Migration: Filesystem → DB
# ═══════════════════════════════════════════════════════════════════════

@router.post("/registry/avatar/migrate-all")
async def migrate_avatars_to_db(user: dict = Depends(get_current_user)) -> dict:
    """One-shot migration: copy all filesystem avatars into PostgreSQL BYTEA.

    For every agent that has a legacy ``avatar_url`` pointing to a local
    file, read the file and store it in ``avatar_data`` / ``avatar_mime``.
    After this runs successfully, all avatars are covered by DB backups.
    """
    require_admin(user)

    rows = await afetch_all(
        "SELECT agent_id, avatar_url FROM agent_registry "
        "WHERE avatar_data IS NULL AND avatar_url != ''"
    )

    migrated = 0
    skipped = 0
    errors: list[str] = []

    for row in rows:
        agent_id = row["agent_id"]
        avatar_url = row["avatar_url"]
        try:
            ok = await _migrate_avatar_to_db(agent_id, avatar_url)
            if ok:
                # Also update avatar_url to the canonical DB-backed form
                await aexecute(
                    "UPDATE agent_registry SET avatar_url=$1 WHERE agent_id=$2",
                    _avatar_url_for(agent_id), agent_id,
                )
                migrated += 1
            else:
                skipped += 1
        except Exception as exc:
            errors.append(f"{agent_id}: {exc}")
            skipped += 1

    return {
        "status": "success",
        "migrated": migrated,
        "skipped": skipped,
        "errors": errors,
    }
