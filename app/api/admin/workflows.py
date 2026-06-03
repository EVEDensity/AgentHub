"""Workflow / agent-route management — full CRUD for DAG execution paths.

Endpoints:
  GET    /workflows              List all workflows
  POST   /workflows              Create a new workflow
  PUT    /workflows/{id}         Update an existing workflow
  DELETE /workflows/{id}         Delete a workflow
  POST   /workflows/{id}/default Set a workflow as the default route
  PATCH  /workflows/{id}/active  Enable or disable a workflow
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from app.db.init_db import now
from app.db.session import aexecute
from app.schemas.common import AgentRouteActiveRequest, AgentRouteRequest
from app.schemas.dag import DAGConfig
from app.services.agent_route_service import agent_route_service
from app.services.auth_service import get_current_user, require_admin, write_audit
from app.services.template_engine import template_engine

router = APIRouter(prefix="/workflows", tags=["admin-workflows"])


# ── LIST ──────────────────────────────────────────────────────────────────


@router.get("")
async def list_workflows(user: dict = Depends(get_current_user)) -> list[dict]:
    """Return all registered agent workflows / routes."""
    require_admin(user)
    return await agent_route_service.list_routes()


# ── CREATE ────────────────────────────────────────────────────────────────


@router.post("")
async def create_workflow(data: AgentRouteRequest, user: dict = Depends(get_current_user)) -> dict:
    """Register a new workflow (agent route) with DAG validation."""
    require_admin(user)
    try:
        route = await agent_route_service.create_route(
            data.name, data.description, data.triggerKeywords, data.nodes, data.isDefault,
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
    """Replace an existing workflow's definition."""
    require_admin(user)

    existing = await agent_route_service.get_route(route_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Workflow not found")

    dag = DAGConfig(total=len(data.nodes), completed=0, nodes=data.nodes)
    template_engine.validate(dag)

    if data.isDefault:
        await aexecute("UPDATE agent_routes SET is_default = 0")
    await aexecute(
        "UPDATE agent_routes SET name = $1, description = $2, trigger_keywords = $3, "
        "nodes_json = $4, is_default = $5, updated_at = $6 WHERE id = $7",
        data.name,
        data.description,
        json.dumps(data.triggerKeywords, ensure_ascii=False),
        json.dumps(data.nodes, ensure_ascii=False),
        1 if data.isDefault else 0,
        now(),
        route_id,
    )

    route = await agent_route_service.get_route(route_id)
    audit_id = write_audit(
        user["id"], "admin", "workflow_update", "L2", "approve",
        {"routeId": route_id, "name": data.name},
    )
    return {"status": "success", "route": route, "auditId": audit_id}


# ── DELETE ────────────────────────────────────────────────────────────────


@router.delete("/{route_id}")
async def delete_workflow(route_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Remove a workflow and its associated routes."""
    require_admin(user)

    existing = await agent_route_service.get_route(route_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Workflow not found")

    await aexecute("DELETE FROM agent_routes WHERE id = $1", route_id)

    audit_id = write_audit(
        user["id"], "admin", "workflow_delete", "L2", "approve",
        {"routeId": route_id, "name": existing["name"]},
    )
    return {"status": "success", "routeId": route_id, "auditId": audit_id}


# ── SET DEFAULT ───────────────────────────────────────────────────────────


@router.post("/{route_id}/default")
async def set_default_workflow(route_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Mark a workflow as the system default route."""
    require_admin(user)
    try:
        route = await agent_route_service.set_default(route_id)
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
    """Enable or disable a workflow."""
    require_admin(user)
    try:
        route = await agent_route_service.set_active(route_id, data.active)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    audit_id = write_audit(
        user["id"], "admin", "workflow_toggle_active", "L1", "approve",
        {"routeId": route_id, "active": data.active},
    )
    return {"status": "success", "route": route, "auditId": audit_id}
