"""MCP Dashboard — system overview metrics aggregation.

Endpoint:
  GET  /api/admin/mcp/dashboard   Aggregated real-time system metrics
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends

from app.db.session import afetch_all, afetch_one
from app.services.auth_service import get_current_user

router = APIRouter(tags=["admin-mcp-dashboard"])


@router.get("/dashboard")
async def mcp_dashboard(user: dict = Depends(get_current_user)) -> dict:
    """Return an aggregated snapshot of all key system metrics.

    This is the primary data source for the MCP overview dashboard.
    It collects data from multiple sources in a single request so the
    frontend can render the full dashboard with one API call.
    """
    now_ts = datetime.now()
    today_start = now_ts.strftime("%Y-%m-%dT00:00:00")
    week_start = (now_ts - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")

    # ── 1. Agent status summary ──────────────────────────────────────
    agent_status_rows = await afetch_all(
        "SELECT status, adapter_type, COUNT(*) AS cnt "
        "FROM agent_registry "
        "WHERE user_id = $1 OR user_id = '' "
        "GROUP BY status, adapter_type "
        "ORDER BY status",
        user["id"],
    )

    agent_summary: dict[str, dict] = {}
    total_agents = 0
    for row in agent_status_rows:
        status = row["status"]
        adapter = row["adapter_type"]
        cnt = int(row["cnt"])
        total_agents += cnt
        if status not in agent_summary:
            agent_summary[status] = {"count": 0, "adapters": {}}
        agent_summary[status]["count"] += cnt
        agent_summary[status]["adapters"][adapter] = (
            agent_summary[status]["adapters"].get(adapter, 0) + cnt
        )

    # Active WebSocket connections (from websocket_manager)
    from app.services.websocket_manager import manager as ws_manager

    active_ws_count = ws_manager.active_connection_count()

    # ── 2. Session statistics ─────────────────────────────────────────
    today_sessions_row = await afetch_one(
        "SELECT COUNT(DISTINCT session_id) AS cnt FROM messages "
        "WHERE created_at >= $1 AND user_id = $2",
        today_start, user["id"],
    )
    today_sessions = int(today_sessions_row["cnt"]) if today_sessions_row else 0

    # ── 3. Message & request throughput ───────────────────────────────
    today_msgs_row = await afetch_one(
        "SELECT COUNT(*) AS cnt FROM messages "
        "WHERE created_at >= $1 AND user_id = $2",
        today_start, user["id"],
    )
    today_messages = int(today_msgs_row["cnt"]) if today_msgs_row else 0

    week_msgs_row = await afetch_one(
        "SELECT COUNT(*) AS cnt FROM messages "
        "WHERE created_at >= $1 AND user_id = $2",
        week_start, user["id"],
    )
    week_messages = int(week_msgs_row["cnt"]) if week_msgs_row else 0

    # ── 4. Token consumption ─────────────────────────────────────────
    today_tokens_row = await afetch_one(
        "SELECT COALESCE(SUM(total_tokens), 0) AS total FROM messages "
        "WHERE created_at >= $1 AND user_id = $2",
        today_start, user["id"],
    )
    today_tokens = int(today_tokens_row["total"]) if today_tokens_row else 0

    week_tokens_row = await afetch_one(
        "SELECT COALESCE(SUM(total_tokens), 0) AS total FROM messages "
        "WHERE created_at >= $1 AND user_id = $2",
        week_start, user["id"],
    )
    week_tokens = int(week_tokens_row["total"]) if week_tokens_row else 0

    # Per-model token breakdown (today)
    per_model_tokens: list[dict] = []
    try:
        model_rows = await afetch_all(
            "SELECT sender, COUNT(*) AS msg_count, COALESCE(SUM(total_tokens), 0) AS tokens "
            "FROM messages "
            "WHERE created_at >= $1 AND user_id = $2 AND sender NOT IN ('user','system') "
            "GROUP BY sender ORDER BY tokens DESC LIMIT 10",
            today_start, user["id"],
        )
        for row in model_rows:
            per_model_tokens.append({
                "model": row["sender"],
                "messages": int(row["msg_count"]),
                "tokens": int(row["tokens"]),
            })
    except Exception:
        per_model_tokens = []

    # ── 5. Tool call statistics ──────────────────────────────────────
    today_tool_calls = 0
    today_tool_success = 0
    top_tools: list[dict] = []
    try:
        tool_count_row = await afetch_one(
            "SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN success=1 THEN 1 ELSE 0 END), 0) AS ok "
            "FROM tool_call_log WHERE created_at >= $1 AND user_id = $2",
            today_start, user["id"],
        )
        if tool_count_row:
            today_tool_calls = int(tool_count_row["total"])
            today_tool_success = int(tool_count_row["ok"])

        top_tool_rows = await afetch_all(
            "SELECT tool_name, COUNT(*) AS cnt, "
            "COALESCE(SUM(CASE WHEN success=1 THEN 1 ELSE 0 END), 0) AS ok "
            "FROM tool_call_log WHERE created_at >= $1 AND user_id = $2 "
            "GROUP BY tool_name ORDER BY cnt DESC LIMIT 5",
            today_start, user["id"],
        )
        for row in top_tool_rows:
            top_tools.append({
                "name": row["tool_name"],
                "count": int(row["cnt"]),
                "successCount": int(row["ok"]),
            })
    except Exception:
        pass  # tool_call_log table may not exist in older deployments

    # ── 6. Performance metrics (from monitor) ─────────────────────────
    perf_snapshot: dict = {}
    try:
        from app.services.performance_monitor import monitor
        perf_snapshot = monitor.snapshot()
    except Exception:
        perf_snapshot = {}

    # ── 7. System resources ──────────────────────────────────────────
    system_info: dict = {"pythonPid": os.getpid()}
    try:
        import psutil as _psutil_module
        system_info.update({
            "cpuPercent": round(_psutil_module.cpu_percent(interval=0.1), 1),
            "memoryPercent": round(_psutil_module.virtual_memory().percent, 1),
            "memoryUsedGB": round(_psutil_module.virtual_memory().used / (1024**3), 2),
            "memoryTotalGB": round(_psutil_module.virtual_memory().total / (1024**3), 2),
        })
    except ImportError:
        system_info.update({
            "cpuPercent": -1,
            "memoryPercent": -1,
            "memoryUsedGB": -1,
            "memoryTotalGB": -1,
            "note": "psutil not installed — install with: pip install psutil",
        })

    # ── 8. Database pool status ──────────────────────────────────────
    from app.db.session import aget_pool

    db_status: dict = {"connected": False, "poolSize": 0, "poolFree": 0}
    try:
        pool = await aget_pool()
        db_status["connected"] = pool is not None
        if pool is not None:
            db_status["poolSize"] = pool._maxsize if hasattr(pool, "_maxsize") else 20
            # Estimate idle connections
            db_status["poolFree"] = getattr(pool, "_freesize", 0)
    except Exception:
        pass

    # ── 9. Recent system events (audit log tail) ─────────────────────
    recent_events: list[dict] = []
    try:
        event_rows = await afetch_all(
            "SELECT id, agent_id AS \"agentId\", action, risk_level AS \"riskLevel\", "
            "decision, timestamp FROM audit_log "
            "WHERE user_id = $1 ORDER BY timestamp DESC LIMIT 20",
            user["id"],
        )
        for row in event_rows:
            recent_events.append(dict(row))
    except Exception:
        pass

    # ── 10. Health status ─────────────────────────────────────────────
    health_ok = True
    health_issues: list[str] = []
    if total_agents == 0:
        health_issues.append("无已注册 Agent")
    if not db_status.get("connected"):
        health_issues.append("数据库连接异常")
    offline_count = agent_summary.get("offline", {}).get("count", 0)
    if offline_count > 0:
        health_issues.append(f"{offline_count} 个 Agent 离线")

    return {
        "timestamp": now_ts.isoformat(timespec="seconds"),
        "health": {
            "status": "healthy" if not health_issues else "degraded",
            "issues": health_issues,
        },
        "agents": {
            "total": total_agents,
            "byStatus": {
                status: {"count": info["count"], "adapters": info["adapters"]}
                for status, info in agent_summary.items()
            },
        },
        "sessions": {
            "activeWebSocket": active_ws_count,
            "today": today_sessions,
        },
        "messages": {
            "today": today_messages,
            "thisWeek": week_messages,
        },
        "tokens": {
            "today": today_tokens,
            "thisWeek": week_tokens,
            "perModel": per_model_tokens,
        },
        "tools": {
            "todayCalls": today_tool_calls,
            "todaySuccess": today_tool_success,
            "topTools": top_tools,
        },
        "performance": perf_snapshot,
        "system": system_info,
        "database": db_status,
        "recentEvents": recent_events,
    }
