"""Workflow / agent-route management — full CRUD for DAG execution paths.

All operations are scoped to the authenticated user so every user sees
only their own workflows.

Endpoints:
  GET    /workflows              List all workflows for current user
  POST   /workflows              Create a new workflow
  PUT    /workflows/{id}         Update an existing workflow
  DELETE /workflows/{id}         Delete a workflow
  POST   /workflows/{id}/default Set a workflow as the default route
  PATCH  /workflows/{id}/active  Enable or disable a workflow
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.db.session import aexecute
from app.schemas.common import AgentRouteActiveRequest
from app.schemas.workflow import AgentRouteRequest, WorkflowDraftRequest, WorkflowValidationRequest
from app.services.agent_route_service import agent_route_service
from app.services.auth_service import get_current_user, require_admin, write_audit
from app.services.context_summary_cache import context_summary_cache
from app.services.workflow_contract import validate_workflow_contract
from app.services.workflow_draft_service import workflow_draft_service
from app.services.workflow_errors import WorkflowVersionConflict

router = APIRouter(prefix="/workflows", tags=["admin-workflows"])


def _uid(user: dict) -> str:
    """Extract the user ID used for per-user scoping."""
    return str(user.get("id", ""))


# ── LIST ──────────────────────────────────────────────────────────────────


@router.get("")
async def list_workflows(user: dict = Depends(get_current_user)) -> list[dict]:
    """Return all workflows belonging to the current user."""
    require_admin(user)
    return await agent_route_service.list_routes(_uid(user))


@router.post("/validate")
async def validate_workflow(data: WorkflowValidationRequest, user: dict = Depends(get_current_user)) -> dict:
    """Validate and normalize an editor graph without persisting it."""
    require_admin(user)
    result = validate_workflow_contract(data.nodes, data.edges, schema_version=data.schemaVersion)
    return result.model_dump(mode="json")


@router.get("/drafts")
async def list_workflow_drafts(user: dict = Depends(get_current_user)) -> list[dict]:
    require_admin(user)
    return await workflow_draft_service.list_drafts(_uid(user))


@router.get("/drafts/{draft_key}")
async def get_workflow_draft(draft_key: str, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    draft = await workflow_draft_service.get_draft(_uid(user), draft_key)
    if not draft:
        raise HTTPException(status_code=404, detail="Workflow draft not found")
    return draft


@router.put("/drafts/{draft_key}")
async def save_workflow_draft(
    draft_key: str, data: WorkflowDraftRequest, user: dict = Depends(get_current_user),
) -> dict:
    require_admin(user)
    try:
        return await workflow_draft_service.save_draft(_uid(user), draft_key, data)
    except WorkflowVersionConflict as exc:
        raise _version_conflict(exc, "workflow_draft_version_conflict") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/drafts/{draft_key}")
async def delete_workflow_draft(draft_key: str, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    if not await workflow_draft_service.delete_draft(_uid(user), draft_key):
        raise HTTPException(status_code=404, detail="Workflow draft not found")
    return {"status": "success", "draftKey": draft_key}


@router.get("/{route_id}")
async def get_workflow(route_id: int, user: dict = Depends(get_current_user)) -> dict:
    require_admin(user)
    route = await agent_route_service.get_route(route_id, _uid(user))
    if not route:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return route


# ── CREATE ────────────────────────────────────────────────────────────────


@router.post("")
async def create_workflow(data: AgentRouteRequest, user: dict = Depends(get_current_user)) -> dict:
    """Register a new workflow (agent route) with DAG validation."""
    require_admin(user)
    uid = _uid(user)
    try:
        route = await agent_route_service.create_route(
            uid,
            data.name,
            data.description,
            data.triggerKeywords,
            data.nodes,
            edges=data.edges,
            is_default=data.isDefault,
            active=data.active,
            schema_version=data.schemaVersion,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_id = write_audit(
        user["id"], "admin", "workflow_create", "L2", "approve",
        {"routeId": route["id"], "name": route["name"]},
    )
    return {"status": "success", "route": route, "auditId": audit_id}


# ── UPDATE ────────────────────────────────────────────────────────────────


@router.put("/{route_id}")
async def update_workflow(route_id: int, data: AgentRouteRequest, user: dict = Depends(get_current_user)) -> dict:
    """Replace an existing workflow's definition (user-scoped)."""
    require_admin(user)
    uid = _uid(user)

    if data.version < 1:
        raise HTTPException(status_code=428, detail="Workflow version is required for updates")
    try:
        route = await agent_route_service.update_route(
            route_id,
            uid,
            name=data.name,
            description=data.description,
            trigger_keywords=data.triggerKeywords,
            nodes=data.nodes,
            edges=data.edges,
            is_default=data.isDefault,
            active=data.active,
            schema_version=data.schemaVersion,
            expected_version=data.version,
        )
    except WorkflowVersionConflict as exc:
        raise _version_conflict(exc, "workflow_version_conflict") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_id = write_audit(
        user["id"], "admin", "workflow_update", "L2", "approve",
        {"routeId": route_id, "name": data.name},
    )
    return {"status": "success", "route": route, "auditId": audit_id}


def _version_conflict(exc: WorkflowVersionConflict, code: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": code,
            "message": str(exc),
            "expectedVersion": exc.expected_version,
            "currentVersion": exc.current_version,
        },
    )


# ── DELETE ────────────────────────────────────────────────────────────────


@router.delete("/{route_id}")
async def delete_workflow(route_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Remove a workflow (user-scoped)."""
    require_admin(user)
    uid = _uid(user)

    existing = await agent_route_service.get_route(route_id, uid)
    if not existing:
        raise HTTPException(status_code=404, detail="Workflow not found")

    await aexecute("DELETE FROM agent_routes WHERE id = $1 AND user_id = $2", route_id, uid)
    context_summary_cache.invalidate("route", uid)

    audit_id = write_audit(
        user["id"], "admin", "workflow_delete", "L2", "approve",
        {"routeId": route_id, "name": existing["name"]},
    )
    return {"status": "success", "routeId": route_id, "auditId": audit_id}


# ── SET DEFAULT ───────────────────────────────────────────────────────────


@router.post("/{route_id}/default")
async def set_default_workflow(route_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Mark a workflow as the user's default route."""
    require_admin(user)
    try:
        route = await agent_route_service.set_default(route_id, _uid(user))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    audit_id = write_audit(
        user["id"], "admin", "workflow_set_default", "L2", "approve",
        {"routeId": route_id},
    )
    return {"status": "success", "route": route, "auditId": audit_id}


# ── TOGGLE ACTIVE ─────────────────────────────────────────────────────────


@router.patch("/{route_id}/active")
async def toggle_workflow_active(
    route_id: int, data: AgentRouteActiveRequest, user: dict = Depends(get_current_user),
) -> dict:
    """Enable or disable a workflow (user-scoped)."""
    require_admin(user)
    try:
        route = await agent_route_service.set_active(route_id, _uid(user), data.active)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    audit_id = write_audit(
        user["id"], "admin", "workflow_toggle_active", "L1", "approve",
        {"routeId": route_id, "active": data.active},
    )
    return {"status": "success", "route": route, "auditId": audit_id}
