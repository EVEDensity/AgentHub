"""MCP Task Monitor — running tasks, task history, DAG progress, cancel.

Endpoints:
  GET    /mcp/tasks               Running task list
  GET    /mcp/tasks/history       Historical tasks (paginated)
  GET    /mcp/tasks/{id}          Task detail with DAG progress
  POST   /mcp/tasks/{id}/cancel   Force-cancel a running task
  GET    /mcp/tasks/templates     DAG templates (CRUD via tasks API)
  POST   /mcp/tasks/templates     Create a DAG template
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.session import afetch_all, afetch_one, aexecute, aexecute_insert
from app.services.auth_service import get_current_user, require_admin, write_audit

router = APIRouter(prefix="/tasks", tags=["admin-mcp-tasks"])


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


@router.get("")
async def list_running_tasks(
    user: dict = Depends(get_current_user),
    status: str = Query("", description="Filter by status: RUNNING, PENDING, COMPLETED, FAILED, CANCELLED"),
    session_id: str = Query("", alias="sessionId"),
) -> list[dict]:
    """Return currently running and recent tasks."""
    require_admin(user)

    conditions = ["1=1"]
    params: list = []
    idx = 0

    if status:
        idx += 1
        conditions.append(f"status = ${idx}")
        params.append(status.upper())
    if session_id:
        idx += 1
        conditions.append(f"session_id = ${idx}")
        params.append(session_id)

    where = " AND ".join(conditions)

    rows = await afetch_all(
        f"SELECT id, session_id AS \"sessionId\", status, dag_json AS dag, "
        f"current_node_id AS \"currentNodeId\", template_id AS \"templateId\", "
        f"agent_route_id AS \"agentRouteId\", "
        f"created_at AS \"createdAt\", updated_at AS \"updatedAt\" "
        f"FROM tasks WHERE {where} ORDER BY updated_at DESC LIMIT 50",
        *params,
    )

    result: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            dag = json.loads(item.get("dag", "{}") or "{}")
            if isinstance(dag, dict):
                item["totalNodes"] = dag.get("total", len(dag.get("nodes", [])))
                completed = dag.get("completed", 0)
                item["completedNodes"] = completed
                item["progressPercent"] = (
                    round(100 * completed / item["totalNodes"], 1)
                    if item["totalNodes"] > 0 else 0
                )
        except (json.JSONDecodeError, TypeError):
            item["totalNodes"] = 0
            item["completedNodes"] = 0
            item["progressPercent"] = 0
        result.append(item)

    return result


@router.get("/history")
async def list_task_history(
    user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=5, le=200, alias="pageSize"),
    status: str = Query(""),
    agent: str = Query(""),
) -> dict:
    """Return paginated historical task execution records."""
    require_admin(user)

    conditions = ["1=1"]
    params: list = []
    idx = 0

    if status:
        idx += 1
        conditions.append(f"success = ${idx}")
        params.append(status.upper() == "SUCCESS")
    if agent:
        idx += 1
        conditions.append(f"assigned_agent = ${idx}")
        params.append(agent)

    where = " AND ".join(conditions)

    count_row = await afetch_one(
        f"SELECT COUNT(*) AS cnt FROM task_execution_history WHERE {where}", *params,
    )
    total = int(count_row["cnt"]) if count_row else 0

    offset = (page - 1) * page_size
    rows = await afetch_all(
        f"SELECT id, task_type AS \"taskType\", assigned_agent AS \"assignedAgent\", "
        f"success, duration_ms AS \"durationMs\", tool_calls_count AS \"toolCallsCount\", "
        f"retry_count AS \"retryCount\", error_type AS \"errorType\", "
        f"session_id AS \"sessionId\", created_at AS \"createdAt\" "
        f"FROM task_execution_history WHERE {where} "
        f"ORDER BY created_at DESC LIMIT {page_size} OFFSET {offset}",
        *params,
    )

    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": max(1, (total + page_size - 1) // page_size) if total > 0 else 0,
    }


@router.get("/{task_id}")
async def task_detail(task_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Return full task detail including DAG node status."""
    require_admin(user)

    row = await afetch_one(
        "SELECT id, session_id AS \"sessionId\", status, dag_json AS dag, "
        "current_node_id AS \"currentNodeId\", template_id AS \"templateId\", "
        "agent_route_id AS \"agentRouteId\", "
        "created_at AS \"createdAt\", updated_at AS \"updatedAt\" "
        "FROM tasks WHERE id = $1",
        task_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")

    result = dict(row)
    try:
        dag = json.loads(result.get("dag", "{}") or "{}")
        result["dagParsed"] = dag
    except (json.JSONDecodeError, TypeError):
        result["dagParsed"] = {"error": "Invalid DAG JSON"}

    # Load execution history for this task
    hist_rows = await afetch_all(
        "SELECT * FROM task_execution_history WHERE session_id = $1 ORDER BY created_at",
        result["sessionId"],
    )
    result["executionHistory"] = [dict(r) for r in hist_rows]

    return result


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Force-cancel a running task."""
    require_admin(user)

    row = await afetch_one(
        "SELECT id, status, session_id FROM tasks WHERE id = $1", task_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")

    if row["status"] not in ("RUNNING", "PENDING"):
        raise HTTPException(status_code=400, detail=f"Task is already {row['status']}")

    await aexecute(
        "UPDATE tasks SET status = 'CANCELLED', updated_at = $1 WHERE id = $2",
        _now(), task_id,
    )

    write_audit(
        user["id"], task_id, "task_cancel",
        "L2", "approve",
        {"taskId": task_id, "previousStatus": row["status"]},
    )

    return {"status": "success", "taskId": task_id, "newStatus": "CANCELLED"}


# ── DAG Template Management ──────────────────────────────────────────

@router.get("/templates/list")
async def list_templates(user: dict = Depends(get_current_user)) -> list[dict]:
    """Return all DAG templates."""
    require_admin(user)

    rows = await afetch_all(
        "SELECT id, name, category, keywords, dag_json AS dag, "
        "usage_count AS \"usageCount\", created_at AS \"createdAt\" "
        "FROM dag_templates ORDER BY usage_count DESC",
    )
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["keywords"] = json.loads(item.get("keywords", "[]") or "[]")
            item["dag"] = json.loads(item.get("dag", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            item["keywords"] = []
            item["dag"] = {}
        result.append(item)
    return result


@router.post("/templates")
async def create_template(body: dict, user: dict = Depends(get_current_user)) -> dict:
    """Create a new DAG template."""
    require_admin(user)

    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    category = (body.get("category") or "custom").strip()
    keywords = json.dumps(body.get("keywords") or [], ensure_ascii=False)
    dag_json = json.dumps(body.get("dag") or {}, ensure_ascii=False)

    tid = await aexecute_insert(
        "INSERT INTO dag_templates (name, category, keywords, dag_json, usage_count, created_at) "
        "VALUES ($1,$2,$3,$4,$5,$6) RETURNING id",
        name, category, keywords, dag_json, 0, _now(),
    )

    write_audit(
        user["id"], f"template/{tid}", "dag_template_create",
        "L1", "approve",
        {"name": name, "category": category},
    )

    return {"status": "success", "id": tid, "name": name}


@router.delete("/templates/{template_id}")
async def delete_template(template_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Delete a DAG template."""
    require_admin(user)

    row = await afetch_one("SELECT id, name FROM dag_templates WHERE id = $1", template_id)
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")

    await aexecute("DELETE FROM dag_templates WHERE id = $1", template_id)

    write_audit(
        user["id"], f"template/{template_id}", "dag_template_delete",
        "L2", "approve",
        {"templateName": row["name"]},
    )

    return {"status": "success", "deleted": template_id}


# ── Agent Route Management ────────────────────────────────────────────

@router.get("/routes/list")
async def list_routes(user: dict = Depends(get_current_user)) -> list[dict]:
    """Return all agent routes."""
    require_admin(user)

    rows = await afetch_all(
        "SELECT id, name, description, trigger_keywords AS keywords, "
        "nodes_json AS nodes, is_default AS \"isDefault\", active, "
        "created_at AS \"createdAt\", updated_at AS \"updatedAt\" "
        "FROM agent_routes ORDER BY is_default DESC, name",
    )
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["keywords"] = json.loads(item.get("keywords", "[]") or "[]")
            item["nodes"] = json.loads(item.get("nodes", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            item["keywords"] = []
            item["nodes"] = []
        result.append(item)
    return result


@router.post("/routes/{route_id}/toggle")
async def toggle_route(route_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Toggle a route's active state."""
    require_admin(user)

    row = await afetch_one("SELECT id, name, active FROM agent_routes WHERE id = $1", route_id)
    if not row:
        raise HTTPException(status_code=404, detail="Route not found")

    new_active = 0 if row["active"] else 1
    await aexecute(
        "UPDATE agent_routes SET active = $1, updated_at = $2 WHERE id = $3",
        new_active, _now(), route_id,
    )

    write_audit(
        user["id"], f"route/{route_id}", "agent_route_toggle",
        "L1", "approve",
        {"routeName": row["name"], "previousActive": bool(row["active"]), "newActive": bool(new_active)},
    )

    return {"status": "success", "routeId": route_id, "active": bool(new_active)}
