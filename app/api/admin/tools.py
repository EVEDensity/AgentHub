from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.db.init_db import now
from app.db.session import dict_rows, get_connection, one_row
from app.schemas.common import (
    AgentToolBindRequest,
    PermissionRuleCreateRequest,
    PermissionRuleUpdateRequest,
    ToolCreateRequest,
    ToolUpdateRequest,
)
from app.services.auth_service import get_current_user

logger = logging.getLogger("agenthub.admin.tools")

router = APIRouter(prefix="/tools", tags=["admin-tools"])


@router.get("/")
async def list_tools(user: dict = Depends(get_current_user)) -> list[dict]:
    """List all tool definitions (built-in + custom)."""
    rows = dict_rows(
        "SELECT id, name, description, category, parameters_json, return_type, "
        "examples_json, risk_level, handler_type, enabled, created_at "
        "FROM tool_definitions ORDER BY category, name"
    )
    result: list[dict] = []
    for r in rows:
        result.append(_format_tool_row(r))
    return result


@router.get("/{tool_id}")
async def get_tool(tool_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Get a single tool definition by ID."""
    row = one_row(
        "SELECT id, name, description, category, parameters_json, return_type, "
        "examples_json, risk_level, handler_type, enabled, created_at "
        "FROM tool_definitions WHERE id=?",
        (tool_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Tool not found")
    return _format_tool_row(row)


@router.post("/")
async def create_tool(data: ToolCreateRequest, user: dict = Depends(get_current_user)) -> dict:
    """Create a new tool definition (for custom tools)."""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM tool_definitions WHERE name=?", (data.name,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail=f"工具 '{data.name}' 已存在")

        cursor = conn.execute(
            "INSERT INTO tool_definitions (name, description, category, parameters_json, "
            "return_type, examples_json, risk_level, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data.name,
                data.description,
                data.category,
                json.dumps(_params_to_list(data.parameters), ensure_ascii=False),
                data.return_type,
                json.dumps(data.examples, ensure_ascii=False),
                data.risk_level,
                1 if data.enabled else 0,
                now(),
            ),
        )
        tool_id = cursor.lastrowid

    return {
        "status": "ok",
        "tool": {"id": tool_id, "name": data.name, "category": data.category},
    }


@router.put("/{tool_id}")
async def update_tool(tool_id: int, data: ToolUpdateRequest, user: dict = Depends(get_current_user)) -> dict:
    """Update a tool definition."""
    row = one_row("SELECT id FROM tool_definitions WHERE id=?", (tool_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Tool not found")

    updates: dict = {}
    if data.description is not None:
        updates["description"] = data.description
    if data.category is not None:
        updates["category"] = data.category
    if data.parameters is not None:
        updates["parameters_json"] = json.dumps(_params_to_list(data.parameters), ensure_ascii=False)
    if data.return_type is not None:
        updates["return_type"] = data.return_type
    if data.examples is not None:
        updates["examples_json"] = json.dumps(data.examples, ensure_ascii=False)
    if data.risk_level is not None:
        updates["risk_level"] = data.risk_level
    if data.enabled is not None:
        updates["enabled"] = 1 if data.enabled else 0

    if not updates:
        return {"status": "ok", "message": "nothing to update"}

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [tool_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE tool_definitions SET {set_clause} WHERE id=?", values)

    return {"status": "ok", "tool_id": tool_id}


@router.delete("/{tool_id}")
async def delete_tool(tool_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Delete a tool definition. Cannot delete built-in tools (handler_type='builtin')."""
    row = one_row("SELECT name, handler_type FROM tool_definitions WHERE id=?", (tool_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Tool not found")
    if row.get("handler_type") == "builtin":
        raise HTTPException(status_code=400, detail="不能删除内置工具")

    with get_connection() as conn:
        conn.execute("DELETE FROM agent_tool_bindings WHERE tool_id=?", (tool_id,))
        conn.execute("DELETE FROM tool_definitions WHERE id=?", (tool_id,))

    return {"status": "ok", "tool_id": tool_id, "name": row["name"]}


@router.get("/bindings/{agent_id}")
async def get_agent_tools(agent_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Get tool bindings for a specific agent."""
    rows = dict_rows(
        "SELECT td.id, td.name, td.category, atb.enabled FROM tool_definitions td "
        "LEFT JOIN agent_tool_bindings atb ON td.id = atb.tool_id AND atb.agent_id=? "
        "WHERE td.enabled=1 ORDER BY td.category, td.name",
        (agent_id,),
    )
    bound = [
        {"tool_id": r["id"], "name": r["name"], "category": r["category"], "enabled": bool(r.get("enabled"))}
        for r in rows if r.get("enabled")
    ]
    available = [
        {"tool_id": r["id"], "name": r["name"], "category": r["category"]}
        for r in rows if not r.get("enabled")
    ]
    return {"agent_id": agent_id, "bound": bound, "available": available}


@router.put("/bindings/{agent_id}")
async def update_agent_tools(agent_id: str, data: AgentToolBindRequest, user: dict = Depends(get_current_user)) -> dict:
    """Update tool bindings for an agent."""
    with get_connection() as conn:
        # Remove existing bindings
        conn.execute("DELETE FROM agent_tool_bindings WHERE agent_id=?", (agent_id,))
        # Add new bindings
        for tool_id in data.tool_ids:
            conn.execute(
                "INSERT OR REPLACE INTO agent_tool_bindings (agent_id, tool_id, enabled) VALUES (?, ?, 1)",
                (agent_id, tool_id),
            )

    return {"status": "ok", "agent_id": agent_id, "tool_count": len(data.tool_ids)}


# ── Permission rules ───────────────────────────────────────────────

@router.get("/permissions/rules")
async def list_permission_rules(user: dict = Depends(get_current_user)) -> list[dict]:
    """List all tool permission rules."""
    rows = dict_rows(
        "SELECT id, agent_id, tool_pattern, path_pattern, behavior, "
        "source, priority, enabled, created_at "
        "FROM tool_permission_rules ORDER BY priority DESC, id ASC"
    )
    result: list[dict] = []
    for r in rows:
        result.append({
            "id": r.get("id"),
            "agentId": r.get("agent_id"),
            "toolPattern": r.get("tool_pattern"),
            "pathPattern": r.get("path_pattern"),
            "behavior": r.get("behavior"),
            "source": r.get("source"),
            "priority": r.get("priority"),
            "enabled": bool(r.get("enabled", 1)),
            "createdAt": r.get("created_at"),
        })
    return result


@router.get("/permissions/rules/{rule_id}")
async def get_permission_rule(rule_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Get a single permission rule."""
    row = one_row(
        "SELECT id, agent_id, tool_pattern, path_pattern, behavior, "
        "source, priority, enabled, created_at "
        "FROM tool_permission_rules WHERE id=?",
        (rule_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Permission rule not found")
    return {
        "id": row.get("id"),
        "agentId": row.get("agent_id"),
        "toolPattern": row.get("tool_pattern"),
        "pathPattern": row.get("path_pattern"),
        "behavior": row.get("behavior"),
        "source": row.get("source"),
        "priority": row.get("priority"),
        "enabled": bool(row.get("enabled", 1)),
        "createdAt": row.get("created_at"),
    }


@router.post("/permissions/rules")
async def create_permission_rule(data: PermissionRuleCreateRequest, user: dict = Depends(get_current_user)) -> dict:
    """Create a new tool permission rule."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO tool_permission_rules "
            "(agent_id, tool_pattern, path_pattern, behavior, source, priority, enabled, created_at) "
            "VALUES (?, ?, ?, ?, 'user', ?, 1, ?)",
            (data.agent_id, data.tool_pattern, data.path_pattern, data.behavior, data.priority, now()),
        )
        rule_id = cursor.lastrowid

    # Reload permission rules in the manager
    try:
        from app.services.tools.permission import permission_manager
        permission_manager.load_rules()
    except Exception:
        pass

    return {"status": "ok", "rule": {"id": rule_id, "toolPattern": data.tool_pattern, "behavior": data.behavior}}


@router.put("/permissions/rules/{rule_id}")
async def update_permission_rule(rule_id: int, data: PermissionRuleUpdateRequest, user: dict = Depends(get_current_user)) -> dict:
    """Update a permission rule."""
    row = one_row("SELECT id FROM tool_permission_rules WHERE id=?", (rule_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Permission rule not found")

    updates: dict = {}
    if data.tool_pattern is not None:
        updates["tool_pattern"] = data.tool_pattern
    if data.path_pattern is not None:
        updates["path_pattern"] = data.path_pattern
    if data.behavior is not None:
        updates["behavior"] = data.behavior
    if data.priority is not None:
        updates["priority"] = data.priority
    if data.enabled is not None:
        updates["enabled"] = 1 if data.enabled else 0

    if not updates:
        return {"status": "ok", "message": "nothing to update"}

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [rule_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE tool_permission_rules SET {set_clause} WHERE id=?", values)

    # Reload permission rules
    try:
        from app.services.tools.permission import permission_manager
        permission_manager.load_rules()
    except Exception:
        pass

    return {"status": "ok", "rule_id": rule_id}


@router.delete("/permissions/rules/{rule_id}")
async def delete_permission_rule(rule_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Delete a permission rule."""
    row = one_row("SELECT id, tool_pattern FROM tool_permission_rules WHERE id=?", (rule_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Permission rule not found")

    with get_connection() as conn:
        conn.execute("DELETE FROM tool_permission_rules WHERE id=?", (rule_id,))

    # Reload permission rules
    try:
        from app.services.tools.permission import permission_manager
        permission_manager.load_rules()
    except Exception:
        pass

    return {"status": "ok", "rule_id": rule_id, "toolPattern": row.get("tool_pattern")}


@router.get("/permissions/rules/agent/{agent_id}")
async def get_agent_permission_rules(agent_id: str, user: dict = Depends(get_current_user)) -> list[dict]:
    """Get permission rules for a specific agent."""
    rows = dict_rows(
        "SELECT id, agent_id, tool_pattern, path_pattern, behavior, "
        "source, priority, enabled, created_at "
        "FROM tool_permission_rules "
        "WHERE enabled=1 AND (agent_id=? OR agent_id='*') "
        "ORDER BY priority DESC, id ASC",
        (agent_id,),
    )
    result: list[dict] = []
    for r in rows:
        result.append({
            "id": r.get("id"),
            "agentId": r.get("agent_id"),
            "toolPattern": r.get("tool_pattern"),
            "pathPattern": r.get("path_pattern"),
            "behavior": r.get("behavior"),
            "source": r.get("source"),
            "priority": r.get("priority"),
            "enabled": bool(r.get("enabled", 1)),
            "createdAt": r.get("created_at"),
        })
    return result


# ── Helpers ────────────────────────────────────────────────────────────

def _params_to_list(params: list) -> list[dict]:
    """Convert ToolParameterSchema list to dict list."""
    return [p.dict() if hasattr(p, "dict") else p for p in params]


def _format_tool_row(row: dict) -> dict:
    """Format a tool_definitions row for API response."""
    params = []
    try:
        params = json.loads(row.get("parameters_json", "[]"))
    except (json.JSONDecodeError, TypeError):
        pass
    examples = []
    try:
        examples = json.loads(row.get("examples_json", "[]"))
    except (json.JSONDecodeError, TypeError):
        pass

    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "description": row.get("description"),
        "category": row.get("category"),
        "parameters": params if isinstance(params, list) else [],
        "returnType": row.get("return_type"),
        "examples": examples if isinstance(examples, list) else [],
        "riskLevel": row.get("risk_level"),
        "handlerType": row.get("handler_type", "builtin"),
        "enabled": bool(row.get("enabled", 1)),
        "createdAt": row.get("created_at"),
    }
