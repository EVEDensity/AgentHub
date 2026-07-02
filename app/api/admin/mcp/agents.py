"""MCP Agent Monitor — live agent status, stats, and control endpoints.

Endpoints:
  GET    /mcp/agents                Agent list with real-time status
  GET    /mcp/agents/{id}/stats     Per-agent statistics
  POST   /mcp/agents/{id}/status    Set agent online/offline
  POST   /mcp/agents/{id}/cancel    Force-cancel running invocations
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from app.db.session import afetch_all, afetch_one, aexecute
from app.services.auth_service import get_current_user, require_admin, write_audit
from app.services.websocket_manager import manager as ws_manager

router = APIRouter(prefix="/agents", tags=["admin-mcp-agents"])


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


@router.get("")
async def list_agents(
    user: dict = Depends(get_current_user),
    domain: str = "",
    status: str = "",
    adapter_type: str = "",
) -> list[dict]:
    """Return all agents with real-time status (user-scoped)."""
    conditions = ["(user_id = $1 OR user_id = '')"]
    params = [user["id"]]
    idx = 1

    if domain:
        idx += 1
        conditions.append(f"domain = ${idx}")
        params.append(domain)
    if status:
        idx += 1
        conditions.append(f"status = ${idx}")
        params.append(status)
    if adapter_type:
        idx += 1
        conditions.append(f"adapter_type = ${idx}")
        params.append(adapter_type)

    where = " AND ".join(conditions)
    rows = await afetch_all(
        f"SELECT agent_id AS \"agentId\", user_id AS \"userId\", domain, status, "
        f"adapter_type AS \"adapterType\", base_model_name AS \"baseModelName\", "
        f"risk_level AS \"riskLevel\", duty_note AS \"dutyNote\", "
        f"display_name AS \"displayName\", COALESCE(avatar_url,'') AS \"avatarUrl\", "
        f"capability_tags AS \"capabilityTags\", base_url AS \"baseUrl\" "
        f"FROM agent_registry WHERE {where} ORDER BY agent_id",
        *params,
    )
    for row in rows:
        try:
            row["capabilityTags"] = json.loads(row.get("capabilityTags", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            row["capabilityTags"] = []

    return rows


@router.get("/{agent_id}/stats")
async def agent_stats(agent_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Return per-agent statistics: recent calls, success rate, token trend."""
    from datetime import datetime, timedelta

    today = datetime.now().strftime("%Y-%m-%dT00:00:00")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")

    # Verify agent exists
    agent = await afetch_one(
        "SELECT agent_id FROM agent_registry WHERE agent_id = $1 AND (user_id = $2 OR user_id = '')",
        agent_id, user["id"],
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Recent call count (today)
    today_count_row = await afetch_one(
        "SELECT COUNT(*) AS cnt FROM messages WHERE sender = $1 AND created_at >= $2",
        agent_id, today,
    )
    today_calls = int(today_count_row["cnt"]) if today_count_row else 0

    # Recent call count (week)
    week_count_row = await afetch_one(
        "SELECT COUNT(*) AS cnt FROM messages WHERE sender = $1 AND created_at >= $2",
        agent_id, week_ago,
    )
    week_calls = int(week_count_row["cnt"]) if week_count_row else 0

    # Token usage (today)
    today_tokens_row = await afetch_one(
        "SELECT COALESCE(SUM(total_tokens),0) AS total FROM messages "
        "WHERE sender = $1 AND created_at >= $2",
        agent_id, today,
    )
    today_tokens = int(today_tokens_row["total"]) if today_tokens_row else 0

    # Tool call stats for this agent
    tool_stats_rows: list[dict] = []
    try:
        tool_stats_rows = await afetch_all(
            "SELECT tool_name, COUNT(*) AS cnt, "
            "COALESCE(SUM(CASE WHEN success=1 THEN 1 ELSE 0 END),0) AS ok "
            "FROM tool_call_log WHERE agent_id = $1 AND created_at >= $2 "
            "GROUP BY tool_name ORDER BY cnt DESC",
            agent_id, today,
        )
    except Exception:
        pass

    # Recent messages (last 10)
    recent_rows = await afetch_all(
        "SELECT substr(created_at, 1, 19) AS \"timestamp\", "
        "COALESCE(total_tokens, 0) AS tokens, type "
        "FROM messages WHERE sender = $1 ORDER BY created_at DESC LIMIT 10",
        agent_id,
    )

    return {
        "agentId": agent_id,
        "todayCalls": today_calls,
        "weekCalls": week_calls,
        "todayTokens": today_tokens,
        "toolStats": [
            {"name": r["tool_name"], "count": int(r["cnt"]),
             "successCount": int(r["ok"])}
            for r in tool_stats_rows
        ],
        "recentCalls": [
            {"timestamp": r["timestamp"], "tokens": int(r["tokens"]),
             "type": r["type"]}
            for r in recent_rows
        ],
    }


@router.post("/{agent_id}/status")
async def set_agent_status(
    agent_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
) -> dict:
    """Set an agent's status (online / offline / sleeping)."""
    require_admin(user)

    new_status = (body.get("status") or "").strip().lower()
    if new_status not in ("online", "offline", "sleeping"):
        raise HTTPException(
            status_code=400,
            detail="status must be one of: online, offline, sleeping",
        )

    result = await aexecute(
        "UPDATE agent_registry SET status = $1 WHERE agent_id = $2 AND user_id = $3",
        new_status, agent_id, user["id"],
    )
    # Also update system agent if user owns the agent
    await aexecute(
        "UPDATE agent_registry SET status = $1 WHERE agent_id = $2 AND user_id = ''",
        new_status, agent_id,
    )

    write_audit(
        user["id"], agent_id, "agent_set_status",
        "L1", "approve",
        {"newStatus": new_status},
    )

    return {"status": "success", "agentId": agent_id, "newStatus": new_status}


@router.post("/{agent_id}/cancel")
async def cancel_agent(
    agent_id: str,
    body: dict | None = None,
    user: dict = Depends(get_current_user),
) -> dict:
    """Force-cancel all running invocations for an agent in a session.

    Body (optional):
        {"sessionId": "session-1"}  — cancel in specific session only
    If sessionId is omitted, cancels across all sessions.
    """
    require_admin(user)

    session_id = (body or {}).get("sessionId", "")
    cancelled = 0

    if session_id:
        # Cancel tokens for this specific session
        tokens = ws_manager.get_tokens_for_session(session_id)
        for token in tokens:
            if not token.cancelled:
                token.cancel()
                cancelled += 1
    else:
        # Cancel all tokens across all sessions
        for sid in list(ws_manager._session_tokens.keys()):
            tokens = ws_manager.get_tokens_for_session(sid)
            for token in tokens:
                if not token.cancelled:
                    token.cancel()
                    cancelled += 1

    write_audit(
        user["id"], agent_id, "agent_cancel",
        "L2", "approve",
        {"sessionId": session_id or "all", "cancelledTokens": cancelled},
    )

    return {
        "status": "success",
        "agentId": agent_id,
        "sessionId": session_id or "all",
        "cancelledTokens": cancelled,
    }


@router.post("/batch/status")
async def batch_set_status(
    body: dict,
    user: dict = Depends(get_current_user),
) -> dict:
    """Batch-set status for multiple agents.

    Body:
        {"agentIds": ["agent1", "agent2"], "status": "offline"}
    """
    require_admin(user)

    agent_ids = body.get("agentIds", [])
    new_status = (body.get("status") or "").strip().lower()
    if new_status not in ("online", "offline", "sleeping"):
        raise HTTPException(status_code=400, detail="Invalid status")
    if not agent_ids or not isinstance(agent_ids, list):
        raise HTTPException(status_code=400, detail="agentIds must be a non-empty array")

    updated = 0
    for aid in agent_ids:
        await aexecute(
            "UPDATE agent_registry SET status = $1 WHERE agent_id = $2 AND user_id = $3",
            new_status, aid, user["id"],
        )
        updated += 1

    write_audit(
        user["id"], "batch", "agent_batch_status",
        "L1", "approve",
        {"agentIds": agent_ids, "newStatus": new_status, "count": updated},
    )

    return {"status": "success", "updated": updated, "newStatus": new_status}


@router.post("/batch/test")
async def batch_test_agents(
    body: dict,
    user: dict = Depends(get_current_user),
) -> dict:
    """Batch test connectivity for multiple agents.

    Body:
        {"agentIds": ["agent1", "agent2"]}
    """
    require_admin(user)

    from app.services.adapter_manager import adapter_manager
    from app.services.secret_service import decrypt_secret

    agent_ids = body.get("agentIds", [])
    if not agent_ids or not isinstance(agent_ids, list):
        raise HTTPException(status_code=400, detail="agentIds must be a non-empty array")

    results: dict[str, dict] = {}
    for aid in agent_ids:
        row = await afetch_one(
            "SELECT adapter_type AS \"adapterType\", base_model_name AS \"baseModelName\", "
            "base_url AS \"baseUrl\", api_key AS \"apiKey\" "
            "FROM agent_registry WHERE agent_id = $1 AND user_id = $2",
            aid, user["id"],
        )
        if not row:
            results[aid] = {"ok": False, "message": "Agent not found"}
            continue

        adapter_type = row["adapterType"] or "mock"
        adapter = adapter_manager.get_adapter(adapter_type)
        test_model = row["baseModelName"] or getattr(adapter, "default_model", "")
        api_key = decrypt_secret(row.get("apiKey") or "")
        base_url = row.get("baseUrl") or ""

        try:
            await adapter.ping(test_model, api_key, base_url)
            await aexecute(
                "UPDATE agent_registry SET status='online' WHERE agent_id=$1 AND user_id=$2",
                aid, user["id"],
            )
            results[aid] = {"ok": True, "message": "OK"}
        except Exception as exc:
            await aexecute(
                "UPDATE agent_registry SET status='offline' WHERE agent_id=$1 AND user_id=$2",
                aid, user["id"],
            )
            results[aid] = {"ok": False, "message": str(exc)}

    write_audit(
        user["id"], "batch", "agent_batch_test",
        "L1", "approve",
        {"agentIds": agent_ids, "results": results},
    )

    return {"status": "success", "results": results}
