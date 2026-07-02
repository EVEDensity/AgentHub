"""MCP Tool Analytics & Permissions — tool call stats, permission rule CRUD.

Endpoints:
  GET    /mcp/tools/analytics       Tool call statistics
  GET    /mcp/tools/permissions      Permission rule list
  POST   /mcp/tools/permissions      Create a permission rule
  PUT    /mcp/tools/permissions/{id} Update a rule
  DELETE /mcp/tools/permissions/{id} Delete a rule
  POST   /mcp/tools/permissions/test  Simulate permission evaluation
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.session import afetch_all, afetch_one, aexecute, aexecute_insert
from app.services.auth_service import get_current_user, require_admin, write_audit

router = APIRouter(prefix="/tools", tags=["admin-mcp-tools"])


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


# ── Tool Analytics ────────────────────────────────────────────────────

@router.get("/analytics")
async def tool_analytics(
    user: dict = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
    sort_by: str = Query("count", alias="sortBy", description="Sort by: count, successRate, avgDuration"),
) -> list[dict]:
    """Return aggregated tool call statistics for the specified time window."""
    require_admin(user)

    from datetime import datetime, timedelta
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")

    rows = await afetch_all(
        "SELECT tool_name, COUNT(*) AS cnt, "
        "COALESCE(SUM(CASE WHEN success=1 THEN 1 ELSE 0 END), 0) AS ok, "
        "COALESCE(AVG(CASE WHEN success=1 THEN duration_ms ELSE NULL END), 0) AS avg_duration, "
        "COALESCE(AVG(CASE WHEN success=0 THEN duration_ms ELSE NULL END), 0) AS avg_duration_fail "
        "FROM tool_call_log WHERE created_at >= $1 "
        "GROUP BY tool_name ORDER BY cnt DESC",
        start_date,
    )

    result: list[dict] = []
    for row in rows:
        total = int(row["cnt"])
        ok = int(row["ok"])
        result.append({
            "toolName": row["tool_name"],
            "totalCalls": total,
            "successCount": ok,
            "failCount": total - ok,
            "successRate": round(100 * ok / total, 1) if total > 0 else 0,
            "avgDurationMs": round(float(row["avg_duration"]), 1),
            "avgDurationFailMs": round(float(row["avg_duration_fail"]), 1),
            "periodDays": days,
        })

    # Sort
    if sort_by == "successRate":
        result.sort(key=lambda x: x["successRate"], reverse=True)
    elif sort_by == "avgDuration":
        result.sort(key=lambda x: x["avgDurationMs"])
    else:
        pass  # already sorted by count DESC from query

    return result


@router.get("/analytics/heatmap")
async def tool_heatmap(
    user: dict = Depends(get_current_user),
    days: int = Query(90, ge=7, le=365),
) -> dict:
    """Return daily tool call counts for a GitHub-style heatmap."""
    require_admin(user)

    from datetime import date, datetime, timedelta
    end = date.today()
    start = end - timedelta(days=days - 1)

    rows = await afetch_all(
        "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS cnt "
        "FROM tool_call_log WHERE created_at >= $1 "
        "GROUP BY day ORDER BY day",
        f"{start.isoformat()}T00:00:00",
    )

    day_map = {row["day"]: int(row["cnt"]) for row in rows}

    days_list: list[dict] = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        days_list.append({"date": key, "count": day_map.get(key, 0)})
        cursor += timedelta(days=1)

    max_count = max((d["count"] for d in days_list), default=1)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "maxCount": max_count,
        "days": days_list,
    }


@router.get("/analytics/anomalies")
async def tool_anomalies(
    user: dict = Depends(get_current_user),
    lookback_days: int = Query(7, alias="lookbackDays"),
) -> list[dict]:
    """Detect recent tool anomalies (spikes in failures or latency)."""
    require_admin(user)

    from datetime import datetime, timedelta
    now = datetime.now()
    recent_start = (now - timedelta(days=lookback_days)).isoformat(timespec="seconds")
    baseline_start = (now - timedelta(days=lookback_days * 2)).isoformat(timespec="seconds")
    baseline_end = recent_start

    # Get recent failure rates per tool
    recent_rows = await afetch_all(
        "SELECT tool_name, COUNT(*) AS cnt, "
        "COALESCE(SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),0) AS fails "
        "FROM tool_call_log WHERE created_at >= $1 "
        "GROUP BY tool_name HAVING COUNT(*) >= 5",
        recent_start,
    )

    # Get baseline failure rates
    baseline_rows = await afetch_all(
        "SELECT tool_name, COUNT(*) AS cnt, "
        "COALESCE(SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),0) AS fails "
        "FROM tool_call_log WHERE created_at >= $1 AND created_at < $2 "
        "GROUP BY tool_name HAVING COUNT(*) >= 5",
        baseline_start, baseline_end,
    )

    baseline_map: dict[str, float] = {}
    for row in baseline_rows:
        if row["cnt"] > 0:
            baseline_map[row["tool_name"]] = row["fails"] / row["cnt"]

    anomalies: list[dict] = []
    for row in recent_rows:
        tool = row["tool_name"]
        recent_rate = row["fails"] / row["cnt"] if row["cnt"] > 0 else 0
        baseline_rate = baseline_map.get(tool, 0.05)

        # Flag if failure rate increased by >2x or by >20 percentage points
        if recent_rate > 0.15 and (recent_rate > baseline_rate * 2 or recent_rate > baseline_rate + 0.2):
            anomalies.append({
                "toolName": tool,
                "recentFailRate": round(recent_rate * 100, 1),
                "baselineFailRate": round(baseline_rate * 100, 1),
                "recentCalls": int(row["cnt"]),
                "severity": "critical" if recent_rate > 0.5 else "warning",
            })

    return anomalies


# ── Permission Rules ──────────────────────────────────────────────────

@router.get("/permissions")
async def list_permissions(user: dict = Depends(get_current_user)) -> list[dict]:
    """Return all tool permission rules."""
    require_admin(user)

    rows = await afetch_all(
        "SELECT id, agent_id AS \"agentId\", tool_pattern AS \"toolPattern\", "
        "path_pattern AS \"pathPattern\", behavior, source, priority, enabled, "
        "created_at AS \"createdAt\" "
        "FROM tool_permission_rules ORDER BY priority DESC, id"
    )
    return [dict(r) for r in rows]


@router.post("/permissions")
async def create_permission(body: dict, user: dict = Depends(get_current_user)) -> dict:
    """Create a new tool permission rule."""
    require_admin(user)

    agent_id = (body.get("agentId") or "*").strip()
    tool_pattern = (body.get("toolPattern") or "*").strip()
    path_pattern = (body.get("pathPattern") or "*").strip()
    behavior = (body.get("behavior") or "ask").strip().lower()
    if behavior not in ("allow", "ask", "deny"):
        raise HTTPException(status_code=400, detail="behavior must be: allow, ask, deny")
    priority = int(body.get("priority", 0))
    source = (body.get("source") or "user").strip()
    enabled = 1 if body.get("enabled", True) else 0

    rid = await aexecute_insert(
        "INSERT INTO tool_permission_rules (agent_id, tool_pattern, path_pattern, "
        "behavior, source, priority, enabled, created_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id",
        agent_id, tool_pattern, path_pattern, behavior, source, priority, enabled, _now(),
    )

    write_audit(
        user["id"], f"perm/{rid}", "tool_permission_create",
        "L2", "approve",
        {"agentId": agent_id, "toolPattern": tool_pattern, "behavior": behavior},
    )

    return {"status": "success", "id": rid}


@router.put("/permissions/{rule_id}")
async def update_permission(rule_id: int, body: dict, user: dict = Depends(get_current_user)) -> dict:
    """Update a tool permission rule."""
    require_admin(user)

    row = await afetch_one("SELECT id FROM tool_permission_rules WHERE id = $1", rule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")

    behavior = (body.get("behavior") or "ask").strip().lower()
    if behavior not in ("allow", "ask", "deny"):
        raise HTTPException(status_code=400, detail="behavior must be: allow, ask, deny")

    priority = int(body.get("priority", 0))
    enabled = 1 if body.get("enabled", True) else 0

    await aexecute(
        "UPDATE tool_permission_rules SET agent_id=$1, tool_pattern=$2, "
        "path_pattern=$3, behavior=$4, priority=$5, enabled=$6 WHERE id=$7",
        (body.get("agentId") or "*").strip(),
        (body.get("toolPattern") or "*").strip(),
        (body.get("pathPattern") or "*").strip(),
        behavior, priority, enabled, rule_id,
    )

    write_audit(
        user["id"], f"perm/{rule_id}", "tool_permission_update",
        "L2", "approve",
        {"ruleId": rule_id, "behavior": behavior},
    )

    return {"status": "success", "id": rule_id}


@router.delete("/permissions/{rule_id}")
async def delete_permission(rule_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Delete a tool permission rule."""
    require_admin(user)

    row = await afetch_one("SELECT id FROM tool_permission_rules WHERE id = $1", rule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")

    await aexecute("DELETE FROM tool_permission_rules WHERE id = $1", rule_id)

    write_audit(
        user["id"], f"perm/{rule_id}", "tool_permission_delete",
        "L2", "approve",
        {"ruleId": rule_id},
    )

    return {"status": "success", "deleted": rule_id}


@router.post("/permissions/test")
async def test_permission(body: dict, user: dict = Depends(get_current_user)) -> dict:
    """Simulate permission evaluation for an agent/tool/path combination."""
    require_admin(user)

    agent_id = (body.get("agentId") or "").strip()
    tool_name = (body.get("toolName") or "").strip()
    file_path = (body.get("path") or "").strip()

    if not agent_id or not tool_name:
        raise HTTPException(status_code=400, detail="agentId and toolName are required")

    # Get all enabled rules, ordered by priority (highest first)
    rules = await afetch_all(
        "SELECT agent_id, tool_pattern, path_pattern, behavior, priority "
        "FROM tool_permission_rules WHERE enabled = 1 ORDER BY priority DESC"
    )

    matched = None
    for rule in rules:
        # Check agent match
        if rule["agent_id"] != "*" and rule["agent_id"] != agent_id:
            # Try wildcard matching
            ag_pat = rule["agent_id"].replace("*", ".*")
            import re as _re
            if not _re.match(f"^{ag_pat}$", agent_id):
                continue

        # Check tool match
        tool_pat = rule["tool_pattern"].replace("*", ".*")
        import re as _re
        if not _re.match(f"^{tool_pat}$", tool_name):
            continue

        # Check path match
        if rule["path_pattern"] != "*" and file_path:
            path_pat = rule["path_pattern"].replace("*", ".*")
            if not _re.match(f"^{path_pat}$", file_path):
                continue

        matched = dict(rule)
        break

    return {
        "agentId": agent_id,
        "toolName": tool_name,
        "path": file_path,
        "matchedRule": matched,
        "behavior": matched["behavior"] if matched else "ask",
        "defaultBehavior": "ask",
    }
