"""Role binding management — maps agent roles to model configurations.

Endpoints:
  POST   /roles   Create or update a role-to-model binding
  GET    /roles   List all role bindings
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.db.init_db import now
from app.db.session import afetch_all, aexecute
from app.schemas.common import RoleBindRequest
from app.services.auth_service import get_current_user, require_admin, write_audit

router = APIRouter(prefix="/roles", tags=["admin-roles"])


@router.post("")
async def upsert_role(data: RoleBindRequest, user: dict = Depends(get_current_user)) -> dict:
    """Create or replace a role-to-model binding."""
    require_admin(user)

    await aexecute(
        "INSERT INTO role_bindings(role, model_config_id, prompt, updated_at) "
        "VALUES ($1, $2, $3, $4) "
        "ON CONFLICT(role) DO UPDATE SET model_config_id=$2, prompt=$3, updated_at=$4",
        data.role, data.modelConfigId, data.prompt, now(),
    )

    audit_id = write_audit(
        user["id"], data.role, "role_bind", "L2", "approve", data.model_dump(),
    )
    return {"status": "success", "auditId": audit_id}


@router.get("")
async def list_roles(user: dict = Depends(get_current_user)) -> list[dict]:
    """Return all role-to-model bindings."""
    require_admin(user)
    return await afetch_all(
        "SELECT role, model_config_id AS \"modelConfigId\", prompt, updated_at AS \"updatedAt\" "
        "FROM role_bindings ORDER BY role"
    )
