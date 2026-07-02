"""User management and user-specific settings API.

Provides:
  - Admin-only user CRUD (list, create, update role, delete)
  - Per-user settings (get/set key-value pairs persisted in user_settings table)
  - Password change for the current user
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db.init_db import now
from app.db.session import aexecute, afetch_all, afetch_one
from app.services.agent_service import seed_default_agents_for_user
from app.services.auth.service import (
    AuthService,
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
)

router = APIRouter(prefix="/api/user", tags=["user"])


# ── Request / Response models ──────────────────────────────────────────


class UserCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    role: str = Field(default="developer", pattern="^(admin|developer|viewer)$")


class UserUpdateRoleRequest(BaseModel):
    role: str = Field(..., pattern="^(admin|developer|viewer)$")


class UserChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=1, max_length=128)


class UserSettingRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=128)
    value: str = Field(..., max_length=4096)


class UserSettingBulkRequest(BaseModel):
    settings: dict[str, str] = Field(default_factory=dict)


# ── Admin: User management ─────────────────────────────────────────────


@router.get("/list")
async def list_users(user: dict = Depends(get_current_user)) -> dict:
    """List all registered users (admin only)."""
    require_admin(user)
    rows = await afetch_all(
        "SELECT id, name, role, created_at FROM users ORDER BY created_at DESC"
    )
    return {"users": [dict(r) for r in rows]}


@router.post("/create")
async def create_user(req: UserCreateRequest, user: dict = Depends(get_current_user)) -> dict:
    """Create a new user (admin only)."""
    require_admin(user)
    try:
        new_user = await AuthService.create_user(req.name, req.password, req.role)
    except HTTPException:
        raise
    # Seed the 6 default multi-agent roles for the new user
    await seed_default_agents_for_user(new_user["id"])
    return {"user": new_user}


@router.put("/{target_user_id}/role")
async def update_user_role(
    target_user_id: str,
    req: UserUpdateRoleRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Change a user's role (admin only)."""
    require_admin(user)

    target = await afetch_one("SELECT id, name, role FROM users WHERE id=$1", target_user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent self-demotion
    if target_user_id == user["id"] and req.role != "admin":
        raise HTTPException(status_code=400, detail="Cannot demote yourself")

    await aexecute(
        "UPDATE users SET role=$1 WHERE id=$2",
        req.role, target_user_id,
    )
    return {"status": "success", "userId": target_user_id, "role": req.role}


@router.delete("/{target_user_id}")
async def delete_user(
    target_user_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Delete a user (admin only)."""
    require_admin(user)

    if target_user_id == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    target = await afetch_one("SELECT id FROM users WHERE id=$1", target_user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Clean up related data
    await aexecute("DELETE FROM agent_registry WHERE user_id=$1", target_user_id)
    await aexecute("DELETE FROM session_members WHERE user_id=$1", target_user_id)
    await aexecute("DELETE FROM user_presence WHERE user_id=$1", target_user_id)
    await aexecute("DELETE FROM user_settings WHERE user_id=$1", target_user_id)
    await aexecute("DELETE FROM audit_log WHERE user_id=$1", target_user_id)
    # Reassign owned sessions to admin
    await aexecute(
        "UPDATE sessions SET owner_id=$1 WHERE owner_id=$2",
        user["id"], target_user_id,
    )
    await aexecute("DELETE FROM users WHERE id=$1", target_user_id)

    return {"status": "success", "userId": target_user_id}


# ── Password management ────────────────────────────────────────────────


@router.post("/change-password")
async def change_password(
    req: UserChangePasswordRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Change the current user's password."""
    db_user = await afetch_one(
        "SELECT password_hash FROM users WHERE id=$1", user["id"]
    )
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    from app.services.auth.service import verify_password

    if not verify_password(req.current_password, db_user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    new_hash = hash_password(req.new_password)
    await aexecute(
        "UPDATE users SET password_hash=$1 WHERE id=$2",
        new_hash, user["id"],
    )
    return {"status": "success"}


# ── Per-user settings ──────────────────────────────────────────────────


@router.get("/settings")
async def get_user_settings(user: dict = Depends(get_current_user)) -> dict:
    """Get all settings for the current user."""
    rows = await afetch_all(
        "SELECT key, value FROM user_settings WHERE user_id=$1",
        user["id"],
    )
    settings: dict[str, str] = {}
    for r in rows:
        settings[r["key"]] = r["value"]
    return {"settings": settings}


@router.put("/settings")
async def set_user_setting(
    req: UserSettingRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Set a single setting key for the current user."""
    await aexecute(
        "INSERT INTO user_settings (user_id, key, value, updated_at) "
        "VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (user_id, key) DO UPDATE SET value=$3, updated_at=$4",
        user["id"], req.key, req.value, now(),
    )
    return {"status": "success", "key": req.key}


@router.post("/settings/bulk")
async def bulk_set_user_settings(
    req: UserSettingBulkRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Set multiple settings at once for the current user."""
    ts = now()
    for key, value in req.settings.items():
        await aexecute(
            "INSERT INTO user_settings (user_id, key, value, updated_at) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (user_id, key) DO UPDATE SET value=$3, updated_at=$4",
            user["id"], key, str(value), ts,
        )
    return {"status": "success", "count": len(req.settings)}


@router.delete("/settings/{key}")
async def delete_user_setting(
    key: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Delete a setting key for the current user."""
    await aexecute(
        "DELETE FROM user_settings WHERE user_id=$1 AND key=$2",
        user["id"], key,
    )
    return {"status": "success", "key": key}
