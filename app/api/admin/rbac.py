# ─────────────────────────────────────────────────────────────────────
# RBAC Administration API (P0.3 — Sprint 4)
# ─────────────────────────────────────────────────────────────────────
# Endpoints for querying the 5-role RBAC system, tool risk matrix, and
# workspace-level ACL management. The role/scope definitions mirror the
# Go constants in services/go/shared/iam/rbac.go — keep them in sync.
#
# Endpoints:
#   GET    /rbac/roles                    List all 5 RBAC roles + scopes
#   GET    /rbac/scopes                   List all available scopes
#   GET    /rbac/risk-matrix              Tool risk classification patterns
#   GET    /rbac/roles/{role}/scopes      Scopes for a specific role
#   GET    /rbac/workspaces/{wid}/acl     List workspace ACLs
#   PUT    /rbac/workspaces/{wid}/acl/{uid}  Set workspace ACL
#   DELETE /rbac/workspaces/{wid}/acl/{uid}  Remove workspace ACL
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.init_db import now
from app.db.session import afetch_all, afetch_one, aexecute
from app.services.auth_service import get_current_user, require_admin, write_audit

router = APIRouter(prefix="/rbac", tags=["admin-rbac"])

# ── RBAC definitions (mirror services/go/shared/iam/rbac.go) ───────────

ROLES: dict[str, dict[str, Any]] = {
    "super_admin": {
        "description": "Cross-tenant break-glass; bypasses all checks.",
        "is_system": True,
        "scopes": ["*"],
    },
    "tenant_admin": {
        "description": "Tenant-scoped administrator: manage members, roles, quotas.",
        "is_system": True,
        "scopes": [
            "session:read", "session:write", "session:create", "session:delete",
            "agent:dispatch", "agent:read",
            "tool:execute", "tool:approve",
            "memory:read", "memory:write",
            "document:upload", "document:read",
            "audit:read", "tenant:manage", "role:manage", "billing:read",
            "workspace:admin", "workspace:read", "model:manage",
        ],
    },
    "agent_operator": {
        "description": (
            "P0.3: Can execute high-risk tools, manage workspaces and models, "
            "but cannot manage tenant members/roles/billing."
        ),
        "is_system": True,
        "scopes": [
            "session:read", "session:write", "session:create",
            "agent:dispatch", "agent:read",
            "tool:execute", "tool:approve",
            "memory:read", "memory:write",
            "document:upload", "document:read",
            "workspace:admin", "workspace:read", "model:manage",
        ],
    },
    "member": {
        "description": "Standard user: create sessions, run agents, use tools.",
        "is_system": True,
        "scopes": [
            "session:read", "session:write", "session:create",
            "agent:dispatch", "agent:read",
            "tool:execute",
            "memory:read", "memory:write",
            "document:upload", "document:read",
            "workspace:read",
        ],
    },
    "viewer": {
        "description": "Read-only: observe sessions and audit logs.",
        "is_system": True,
        "scopes": [
            "session:read", "agent:read", "document:read", "audit:read",
            "workspace:read",
        ],
    },
}

ALL_SCOPES: list[str] = [
    "session:read", "session:write", "session:create", "session:delete",
    "agent:dispatch", "agent:read",
    "tool:execute", "tool:approve",
    "memory:read", "memory:write",
    "document:upload", "document:read",
    "audit:read", "tenant:manage", "role:manage", "billing:read",
    "workspace:admin", "workspace:read", "model:manage",
]

# ── Tool risk matrix (mirror services/go/shared/iam/abac.go) ───────────

RISK_LEVELS = ["low", "normal", "high", "critical"]

BUILTIN_TOOL_RISK: list[dict[str, Any]] = [
    {"pattern": "rm -rf", "risk": "critical", "requires_confirmation": True},
    {"pattern": "rm -fr", "risk": "critical", "requires_confirmation": True},
    {"pattern": "rmdir /", "risk": "critical", "requires_confirmation": True},
    {"pattern": "mkfs", "risk": "critical", "requires_confirmation": True},
    {"pattern": "dd if=", "risk": "critical", "requires_confirmation": True},
    {"pattern": "shutdown", "risk": "critical", "requires_confirmation": True},
    {"pattern": "git push --force", "risk": "critical", "requires_confirmation": True},
    {"pattern": ":(){ :|:& };", "risk": "critical", "requires_confirmation": True},
    {"pattern": "reboot", "risk": "high", "requires_confirmation": True},
    {"pattern": "docker", "risk": "high", "requires_confirmation": True},
    {"pattern": "kubectl", "risk": "high", "requires_confirmation": True},
    {"pattern": "helm", "risk": "high", "requires_confirmation": True},
    {"pattern": "git push", "risk": "high", "requires_confirmation": True},
    {"pattern": "curl ", "risk": "normal", "requires_confirmation": False},
    {"pattern": "wget ", "risk": "normal", "requires_confirmation": False},
    {"pattern": "scp ", "risk": "normal", "requires_confirmation": False},
    {"pattern": "chmod", "risk": "normal", "requires_confirmation": False},
    {"pattern": "chown", "risk": "normal", "requires_confirmation": False},
]

# ── Pydantic models ────────────────────────────────────────────────────


class WorkspaceACLRequest(BaseModel):
    role: str
    permissions: list[str] = []


# ── Endpoints ──────────────────────────────────────────────────────────


@router.get("/roles")
async def list_rbac_roles(user: dict = Depends(get_current_user)) -> list[dict]:
    """List all 5 RBAC roles with their default scopes."""
    require_admin(user)
    return [
        {"name": name, **info}
        for name, info in ROLES.items()
    ]


@router.get("/scopes")
async def list_scopes(user: dict = Depends(get_current_user)) -> list[str]:
    """List all available scope strings."""
    require_admin(user)
    return ALL_SCOPES


@router.get("/roles/{role}/scopes")
async def get_role_scopes(role: str, user: dict = Depends(get_current_user)) -> dict:
    """Get scopes for a specific role."""
    require_admin(user)
    if role not in ROLES:
        raise HTTPException(status_code=404, detail=f"Unknown role: {role}")
    info = ROLES[role]
    return {"role": role, "scopes": info["scopes"], "description": info["description"]}


@router.get("/risk-matrix")
async def get_risk_matrix(user: dict = Depends(get_current_user)) -> dict:
    """Return the builtin tool risk classification matrix."""
    require_admin(user)
    return {
        "risk_levels": RISK_LEVELS,
        "builtin_patterns": BUILTIN_TOOL_RISK,
        "role_risk_allowance": {
            "super_admin": "critical",
            "tenant_admin": "critical",
            "agent_operator": "critical",
            "member": "high (with confirmation)",
            "viewer": "low (no execution)",
        },
    }


@router.get("/workspaces/{workspace_id}/acl")
async def list_workspace_acl(
    workspace_id: str, user: dict = Depends(get_current_user)
) -> list[dict]:
    """List all ACL entries for a workspace."""
    require_admin(user)
    return await afetch_all(
        'SELECT user_id, role, permissions, joined_at '
        'FROM platform_workspace_members '
        'WHERE workspace_id = $1 '
        'ORDER BY joined_at',
        workspace_id,
    )


@router.put("/workspaces/{workspace_id}/acl/{user_id}")
async def set_workspace_acl(
    workspace_id: str,
    user_id: str,
    acl: WorkspaceACLRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Set or update a user's ACL in a workspace."""
    require_admin(user)

    if acl.role not in ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role: {acl.role}. Valid roles: {list(ROLES.keys())}",
        )

    permissions_json = json.dumps(acl.permissions)

    await aexecute(
        'INSERT INTO platform_workspace_members (workspace_id, user_id, role, permissions, joined_at) '
        'VALUES ($1, $2, $3, $4::jsonb, $5) '
        'ON CONFLICT (workspace_id, user_id) DO UPDATE SET role=$3, permissions=$4::jsonb',
        workspace_id, user_id, acl.role, permissions_json, now(),
    )

    audit_id = write_audit(
        user["id"],
        workspace_id,
        "workspace_acl_set",
        "L2",
        "approve",
        {"user_id": user_id, "role": acl.role, "permissions": acl.permissions},
    )

    return {
        "status": "success",
        "workspace_id": workspace_id,
        "user_id": user_id,
        "role": acl.role,
        "permissions": acl.permissions,
        "audit_id": audit_id,
    }


@router.delete("/workspaces/{workspace_id}/acl/{user_id}")
async def remove_workspace_acl(
    workspace_id: str,
    user_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Remove a user's ACL from a workspace."""
    require_admin(user)

    result = await aexecute(
        'DELETE FROM platform_workspace_members WHERE workspace_id = $1 AND user_id = $2',
        workspace_id, user_id,
    )

    if result and result.split()[-1] == "0":
        raise HTTPException(status_code=404, detail="ACL not found")

    audit_id = write_audit(
        user["id"],
        workspace_id,
        "workspace_acl_remove",
        "L2",
        "approve",
        {"user_id": user_id},
    )

    return {
        "status": "success",
        "workspace_id": workspace_id,
        "user_id": user_id,
        "audit_id": audit_id,
    }
