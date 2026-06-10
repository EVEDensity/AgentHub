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
from app.services.agent_service import seed_default_agents_for_user
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

# ── Deterministic SVG fallback for agents without an uploaded avatar ──
# When an agent has no avatar_data in DB and no legacy file on disk, we
# generate a unique, deterministic SVG avatar from its agent_id.  This
# guarantees that *every* agent (including custom ones the admin creates)
# always has a unique, visually-distinct avatar — without requiring a
# manual upload.

# Curated palette: 12 colors with strong contrast against the warm UI.
# Each agent_id is hashed to a stable (color, second-color) pair, so the
# same agent_id always renders the same avatar (consistent across users)
# and different agent_ids render different avatars (uniqueness per agent).
_AVATAR_PALETTE: list[tuple[str, str]] = [
    ("#f97316", "#fb923c"),  # orange
    ("#ec4899", "#f472b6"),  # pink
    ("#8b5cf6", "#a78bfa"),  # violet
    ("#3b82f6", "#60a5fa"),  # blue
    ("#10b981", "#34d399"),  # emerald
    ("#f59e0b", "#fbbf24"),  # amber
    ("#06b6d4", "#22d3ee"),  # cyan
    ("#ef4444", "#f87171"),  # red
    ("#14b8a6", "#2dd4bf"),  # teal
    ("#a855f7", "#c084fc"),  # purple
    ("#84cc16", "#a3e635"),  # lime
    ("#f43f5e", "#fb7185"),  # rose
]


def _generate_default_avatar_svg(agent_id: str) -> tuple[bytes, str]:
    """Generate a deterministic, unique SVG avatar for the given agent_id.

    Returns ``(bytes, mime)``.  The avatar is a circular gradient with the
    first character of the agent_id rendered in white.  The same agent_id
    always produces the same SVG, but different agent_ids produce visually
    distinct avatars.
    """
    if not agent_id:
        agent_id = "?"

    # Stable hash → palette index (so the same agent always uses the same color)
    h = 0
    for ch in agent_id:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    primary, secondary = _AVATAR_PALETTE[h % len(_AVATAR_PALETTE)]
    # Vary the rotation by another hash mod to make repeated palette slots
    # still feel distinct.
    angle = (h >> 8) % 360

    # First character — supports both ASCII and CJK
    ch = agent_id[0].upper()

    # Build a minimal, dependency-free SVG (90×90 is plenty for a 40×40 chip)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="90" height="90" viewBox="0 0 90 90">'
        f'<defs>'
        f'<linearGradient id="g" gradientTransform="rotate({angle} 0.5 0.5)">'
        f'<stop offset="0%" stop-color="{primary}"/>'
        f'<stop offset="100%" stop-color="{secondary}"/>'
        f'</linearGradient>'
        f'</defs>'
        f'<circle cx="45" cy="45" r="45" fill="url(#g)"/>'
        f'<text x="45" y="45" fill="#ffffff" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,PingFang SC,Microsoft YaHei,sans-serif" '
        f'font-size="42" font-weight="700" text-anchor="middle" dominant-baseline="central">{ch}</text>'
        f'</svg>'
    )
    return svg.encode("utf-8"), "image/svg+xml"


def _avatar_url_for(agent_id: str, user_id: str = "") -> str:
    """Return the canonical DB-backed avatar URL for an agent.

    Includes ``user_id`` as a query parameter so the serve endpoint can
    scope the DB lookup to the correct user — this prevents avatar leakage
    when two users have uploaded different avatars for the same agent_id.
    """
    base = f"/api/agent/registry/avatar/{agent_id}"
    return f"{base}?u={user_id}" if user_id else base


def _content_type_for(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    return _MIME_BY_EXT.get(ext, "image/png")


async def _migrate_avatar_to_db(agent_id: str, avatar_url: str, user_id: str = "") -> bool:
    """Try to read an avatar from filesystem (legacy URL) → store in DB.

    Returns True if migration succeeded, False otherwise.
    """
    if not avatar_url or "/registry/avatar/" not in avatar_url:
        return False

    # Extract filename from legacy URL like /api/agent/registry/avatar/filename.png
    filename = avatar_url.rsplit("/", 1)[-1]
    # Strip query params if present (e.g., filename.png?u=xxx)
    if "?" in filename:
        filename = filename.split("?", 1)[0]
    safe_name = Path(filename).name
    if safe_name != filename or ".." in filename:
        return False

    file_path = AVATAR_DIR / safe_name
    if not await aexists(file_path):
        return False

    try:
        content = await aread_bytes(file_path)
        mime = _content_type_for(safe_name)
        if user_id:
            await aexecute(
                "UPDATE agent_registry SET avatar_data=$1, avatar_mime=$2 WHERE agent_id=$3 AND user_id=$4",
                content, mime, agent_id, user_id,
            )
        else:
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
async def registry(user: dict = Depends(get_current_user)) -> list[dict]:
    user_id = user["id"]
    # Always prefer the canonical agent_id-based URL so the server endpoint
    # can fall back to the deterministic SVG when no avatar is uploaded.
    # This avoids broken-image <img> tags when an agent has a stale
    # avatar_url pointing at a file that no longer exists.
    _AVATAR_SQL = (
        "('/api/agent/registry/avatar/' || a.agent_id || '?u=' || a.user_id) AS \"avatarUrl\""
    )
    _REGISTRY_COLS = (
        "a.agent_id AS \"agentId\",a.domain,a.status,"
        "a.adapter_type AS \"adapterType\","
        "a.base_model_name AS \"baseModelName\",a.risk_level AS \"rankLevel\","
        "a.duty_note AS \"dutyNote\",a.display_name AS \"displayName\","
        + _AVATAR_SQL + ","
        "a.capability_tags AS \"capabilityTags\","
        "a.base_url AS \"baseUrl\","
        "(a.avatar_data IS NULL) AS \"avatarMissing\""
    )
    rows = await afetch_all(
        f"SELECT {_REGISTRY_COLS} FROM agent_registry a "
        "WHERE a.user_id=$1 ORDER BY a.agent_id",
        user_id,
    )
    # Auto-seed 6 default agents if this user has none yet
    if not rows:
        await seed_default_agents_for_user(user_id)
        rows = await afetch_all(
            f"SELECT {_REGISTRY_COLS} FROM agent_registry a "
            "WHERE a.user_id=$1 ORDER BY a.agent_id",
            user_id,
        )
    # ── Lazy avatar migration: copy avatar data from system agents ──
    # Runs once per user whose agents were seeded before the avatar-copy
    # logic was added to seed_default_agents_for_user.
    if any(row.get("avatarMissing") for row in rows):
        # Step 1: Batch-copy avatars from system agents (user_id='')
        await aexecute(
            "UPDATE agent_registry AS target "
            "SET avatar_data = source.avatar_data, "
            "    avatar_mime = source.avatar_mime, "
            "    avatar_url   = source.avatar_url "
            "FROM agent_registry AS source "
            "WHERE target.agent_id = source.agent_id "
            "  AND target.user_id = $1 "
            "  AND source.user_id = '' "
            "  AND source.avatar_data IS NOT NULL "
            "  AND target.avatar_data IS NULL",
            user_id,
        )
        # Step 2: For agents that still have no avatar (system agent also
        # had no bytes), generate a deterministic SVG and store it.
        still_missing = await afetch_all(
            "SELECT agent_id FROM agent_registry "
            "WHERE user_id=$1 AND avatar_data IS NULL",
            user_id,
        )
        for sm in still_missing:
            agent_id = sm["agent_id"]
            svg_bytes, mime = _generate_default_avatar_svg(agent_id)
            await aexecute(
                "UPDATE agent_registry SET avatar_data=$1, avatar_mime=$2 "
                "WHERE agent_id=$3 AND user_id=$4",
                svg_bytes, mime, agent_id, user_id,
            )
        # Re-read so in-memory dicts reflect the new avatar state
        rows = await afetch_all(
            f"SELECT {_REGISTRY_COLS} FROM agent_registry a "
            "WHERE a.user_id=$1 ORDER BY a.agent_id",
            user_id,
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
        migrated = await _migrate_avatar_to_db(agent_id, avatar_url, user["id"])
        if migrated:
            avatar_url = _avatar_url_for(agent_id, user["id"])

    try:
        await aexecute(
            "INSERT INTO agent_registry(agent_id,user_id,domain,status,adapter_type,base_model_name,"
            "risk_level,duty_note,display_name,avatar_url,capability_tags,base_url,api_key) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
            agent_id,
            user["id"],
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
        "DELETE FROM agent_registry WHERE agent_id=$1 AND user_id=$2 RETURNING agent_id",
        agent_id, user["id"],
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

    exists = await afetch_one(
        "SELECT agent_id FROM agent_registry WHERE agent_id=$1 AND user_id=$2",
        agent_id, user["id"],
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    # ── Handle avatar: migrate from filesystem URL → DB storage ─────
    avatar_url = data.avatarUrl.strip()
    if avatar_url and "/registry/avatar/" in avatar_url:
        migrated = await _migrate_avatar_to_db(agent_id, avatar_url, user["id"])
        if migrated:
            avatar_url = _avatar_url_for(agent_id, user["id"])

    tags_json = json.dumps(data.capabilityTags or [], ensure_ascii=False)
    if data.apiKey.strip():
        await aexecute(
            "UPDATE agent_registry SET domain=$1,adapter_type=$2,base_model_name=$3,"
            "risk_level=$4,duty_note=$5,display_name=$6,avatar_url=$7,"
            "capability_tags=$8,base_url=$9,api_key=$10 WHERE agent_id=$11 AND user_id=$12",
            data.domain.strip(), data.adapterType, data.baseModelName.strip(),
            data.rankLevel, data.dutyNote.strip(), data.displayName.strip(),
            avatar_url, tags_json, data.baseUrl.strip(),
            encrypt_secret(data.apiKey.strip()), agent_id, user["id"],
        )
    else:
        await aexecute(
            "UPDATE agent_registry SET domain=$1,adapter_type=$2,base_model_name=$3,"
            "risk_level=$4,duty_note=$5,display_name=$6,avatar_url=$7,"
            "capability_tags=$8,base_url=$9 WHERE agent_id=$10 AND user_id=$11",
            data.domain.strip(), data.adapterType, data.baseModelName.strip(),
            data.rankLevel, data.dutyNote.strip(), data.displayName.strip(),
            avatar_url, tags_json, data.baseUrl.strip(), agent_id, user["id"],
        )

    audit_id = write_audit(user["id"], agent_id, "agent_update", "L2", "approve", {**data.model_dump(), "apiKey": "***" if data.apiKey else ""})
    return {"status": "success", "agentId": agent_id, "auditId": audit_id}


@router.post("/registry/{agent_id}/test")
async def test_agent_model(agent_id: str, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    row = await afetch_one(
        "SELECT agent_id AS \"agentId\",adapter_type AS \"adapterType\",base_model_name AS \"baseModelName\","
        "base_url AS \"baseUrl\",api_key AS \"apiKey\" FROM agent_registry "
        "WHERE agent_id=$1 AND user_id=$2",
        agent_id, user["id"],
    )
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
# Local Agent Discovery & Registration
# ═══════════════════════════════════════════════════════════════════════


@router.get("/local/discover")
async def discover_local_agents(user: dict = Depends(get_current_user)) -> dict:
    """Scan the server's PATH for installed local AI CLI tools.

    Returns a list of :class:`LocalAgentCandidate` objects, each
    indicating whether the tool was found and is healthy.
    """
    require_admin(user)
    from app.services.local_agent_discovery import discover_local_agents as _discover

    candidates = await _discover()
    # Also mark which ones are already registered for this user
    registered = await afetch_all(
        "SELECT agent_id, adapter_type, status FROM agent_registry "
        "WHERE user_id=$1 AND adapter_type IN ($2,$3,$4)",
        user["id"], "local_claude", "local_codex", "local_openclaw",
    )
    registered_map: dict[str, dict] = {}
    for row in registered:
        registered_map[row["adapter_type"]] = {
            "agentId": row["agent_id"],
            "status": row["status"],
        }

    result: list[dict] = []
    for c in candidates:
        entry = {
            "adapterType": c.adapter_type,
            "displayName": c.display_name,
            "binary": c.binary,
            "installPath": c.install_path,
            "version": c.version,
            "installed": c.installed,
            "healthy": c.healthy,
            "errorMessage": c.error_message,
            "capabilities": c.capabilities,
            "headlessCommand": c.headless_command,
        }
        reg = registered_map.get(c.adapter_type)
        if reg:
            entry["registered"] = True
            entry["registeredAgentId"] = reg["agentId"]
            entry["registeredStatus"] = reg["status"]
        else:
            entry["registered"] = False
        result.append(entry)

    return {"candidates": result, "total": len(result)}


@router.post("/local/register")
async def register_local_agent(data: dict, user: dict = Depends(get_current_user)) -> dict:
    """Register a discovered local agent into the agent_registry.

    Expected JSON body::

        {
            "adapterType": "local_claude",
            "agentId": "my-claude",       // optional, auto-derived if empty
            "domain": "codegen",          // optional
            "displayName": "My Claude",   // optional
            "riskLevel": "L1",            // optional
            "capabilityTags": ["code"],   // optional
        }
    """
    require_admin(user)

    from app.services.local_agent_discovery import (
        DISCOVERY_MAP,
        discover_local_agents,
        register_local_agent,
    )

    adapter_type = (data.get("adapterType") or "").strip()
    if adapter_type not in DISCOVERY_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown local adapter type: {adapter_type}. "
                   f"Supported: {', '.join(DISCOVERY_MAP.keys())}",
        )

    # Verify the tool is installed before registering
    candidates = await discover_local_agents()
    candidate = next((c for c in candidates if c.adapter_type == adapter_type), None)
    if not candidate or not candidate.installed:
        raise HTTPException(
            status_code=400,
            detail=f"未检测到已安装的 {DISCOVERY_MAP[adapter_type]['display_name']}。"
                   f"请先安装对应的 CLI 工具。",
        )

    result = await register_local_agent(
        candidate,
        user_id=user["id"],
        domain=data.get("domain", ""),
        agent_id=data.get("agentId", ""),
        risk_level=data.get("riskLevel", "L1"),
        duty_note=data.get("displayName", ""),
        base_model_name=data.get("baseModelName", candidate.display_name),
    )

    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result.get("error", "注册失败"))

    audit_id = write_audit(
        user["id"], result["agentId"], "local_agent_register", "L1", "approve",
        {"adapterType": adapter_type, "binary": candidate.binary, "version": candidate.version},
    )
    result["auditId"] = audit_id
    return result


@router.get("/local/status")
async def get_local_agent_status(user: dict = Depends(get_current_user)) -> dict:
    """Health-check all registered local agents for the current user.

    Re-runs ``<binary> --version`` for each registered local agent
    and updates the ``status`` column accordingly.
    """
    require_admin(user)

    from app.services.local_agent_discovery import check_agent_health, DISCOVERY_MAP

    rows = await afetch_all(
        "SELECT agent_id, adapter_type FROM agent_registry "
        "WHERE user_id=$1 AND adapter_type IN ($2,$3,$4)",
        user["id"], "local_claude", "local_codex", "local_openclaw",
    )

    results: list[dict] = []
    for row in rows:
        adapter_type = row["adapter_type"]
        agent_id = row["agent_id"]
        cfg = DISCOVERY_MAP.get(adapter_type, {})
        binary = cfg.get("binary", "")

        if not binary:
            results.append({
                "agentId": agent_id,
                "adapterType": adapter_type,
                "online": False,
                "message": "未找到二进制配置",
            })
            continue

        import shutil
        install_path = shutil.which(binary) or binary
        version, healthy, error = await check_agent_health(
            install_path, adapter_type,
        )

        new_status = "online" if healthy else "offline"
        await aexecute(
            "UPDATE agent_registry SET status=$1 WHERE agent_id=$2 AND user_id=$3",
            new_status, agent_id, user["id"],
        )

        results.append({
            "agentId": agent_id,
            "adapterType": adapter_type,
            "online": healthy,
            "version": version,
            "installPath": install_path,
            "message": "" if healthy else error,
        })

    return {"agents": results, "total": len(results)}


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
        existing = await afetch_one(
            "SELECT agent_id FROM agent_registry WHERE agent_id=$1 AND user_id=$2",
            agentId.strip(), user["id"],
        )
        if existing:
            await aexecute(
                "UPDATE agent_registry SET avatar_data=$1, avatar_mime=$2, avatar_url=$3 "
                "WHERE agent_id=$4 AND user_id=$5",
                content, mime, _avatar_url_for(agentId.strip(), user["id"]),
                agentId.strip(), user["id"],
            )
            # Also save a local copy for backward compat
            try:
                avatar_path = AVATAR_DIR / f"{agentId.strip()}{ext}"
                await awrite_bytes(avatar_path, content)
            except Exception:
                pass
            return {
                "avatarUrl": _avatar_url_for(agentId.strip(), user["id"]),
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
async def get_avatar(ident: str, u: str = ""):
    """Serve an avatar image.

    Lookup order:
    1. **agent_id + user_id** (``?u=`` query param) — DB-backed, per-user.
    2. **agent_id** only — DB-backed, first match (legacy fallback).
    3. **filename** — serve from local disk (legacy fallback).
    4. **deterministic SVG** — auto-generated from ``ident`` if it is a
       known agent_id and no uploaded avatar exists.  Guarantees every
       agent (including the legacy/custom ones the admin creates) renders
       a unique, stable avatar without requiring a manual upload.
    """
    from fastapi.responses import Response

    # ── 1. Try DB lookup by agent_id + user_id (per-user avatar) ──
    if u:
        row = await afetch_one(
            "SELECT avatar_data, avatar_mime FROM agent_registry WHERE agent_id=$1 AND user_id=$2",
            ident, u,
        )
        if row and row.get("avatar_data") is not None:
            content = row["avatar_data"]
            mime = row.get("avatar_mime") or "image/png"
            if isinstance(content, memoryview):
                content = bytes(content)
            return Response(content=content, media_type=mime)

        # Per-user lookup failed — skip the agent_id-only DB query to
        # prevent leaking another user's custom avatar.  Fall through
        # directly to the filesystem / deterministic-SVG fallbacks.

    else:
        # ── 1b. DB lookup by agent_id only (legacy, no user scope) ─────
        # Only reached when no `u` query param is present (old URL format).
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
    if ".." not in safe_name and "/" not in safe_name and "\\" not in safe_name:
        avatar_path = AVATAR_DIR / safe_name
        if await aexists(avatar_path):
            content = await aread_bytes(avatar_path)
            return Response(content=content, media_type=_content_type_for(safe_name))

    # ── 3. Deterministic SVG fallback (so every agent always has an avatar) ──
    # Only emit the auto-SVG when the ident looks like an agent_id (no file
    # extension).  This keeps legacy /registry/avatar/<file>.<ext> URLs
    # returning a real 404 instead of an SVG masquerading as the file.
    if "." not in ident and "/" not in ident and "\\" not in ident:
        svg_bytes, mime = _generate_default_avatar_svg(ident)
        return Response(content=svg_bytes, media_type=mime)

    raise HTTPException(status_code=404, detail="Avatar not found")


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

    current_uid = user["id"]

    rows = await afetch_all(
        "SELECT agent_id, avatar_url FROM agent_registry "
        "WHERE user_id=$1 AND avatar_data IS NULL AND avatar_url != ''",
        current_uid,
    )

    migrated = 0
    skipped = 0
    errors: list[str] = []

    for row in rows:
        agent_id = row["agent_id"]
        avatar_url = row["avatar_url"]
        try:
            ok = await _migrate_avatar_to_db(agent_id, avatar_url, current_uid)
            if ok:
                # Also update avatar_url to the canonical DB-backed form
                await aexecute(
                    "UPDATE agent_registry SET avatar_url=$1 WHERE agent_id=$2 AND user_id=$3",
                    _avatar_url_for(agent_id, current_uid), agent_id, current_uid,
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
