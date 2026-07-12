"""MCP Alert Rules & History — configurable alert rules and triggered alert log.

Endpoints:
  GET    /mcp/alerts/rules          List alert rules
  POST   /mcp/alerts/rules          Create an alert rule
  PUT    /mcp/alerts/rules/{id}     Update a rule
  DELETE /mcp/alerts/rules/{id}     Delete a rule
  GET    /mcp/alerts/history        Alert history (paginated)
  POST   /mcp/alerts/history/{id}/ack  Acknowledge an alert
  POST   /mcp/alerts/evaluate       Manually trigger rule evaluation
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.session import afetch_all, afetch_one, aexecute, aexecute_insert
from app.services.auth_service import get_current_user, require_admin, write_audit

router = APIRouter(prefix="/alerts", tags=["admin-mcp-alerts"])


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


# ── Alert Rules CRUD ──────────────────────────────────────────────────

@router.get("/rules")
async def list_rules(
    user: dict = Depends(get_current_user),
    enabled_only: bool = Query(False, alias="enabledOnly"),
) -> list[dict]:
    """Return all alert rules."""
    require_admin(user)

    if enabled_only:
        rows = await afetch_all(
            "SELECT id, name, description, rule_type AS \"ruleType\", "
            "condition_json AS \"condition\", severity, enabled, "
            "notify_channels AS \"notifyChannels\", "
            "silence_window_seconds AS \"silenceWindowSeconds\", "
            "created_at AS \"createdAt\", updated_at AS \"updatedAt\" "
            "FROM alert_rules WHERE enabled = 1 ORDER BY severity DESC, id"
        )
    else:
        rows = await afetch_all(
            "SELECT id, name, description, rule_type AS \"ruleType\", "
            "condition_json AS \"condition\", severity, enabled, "
            "notify_channels AS \"notifyChannels\", "
            "silence_window_seconds AS \"silenceWindowSeconds\", "
            "created_at AS \"createdAt\", updated_at AS \"updatedAt\" "
            "FROM alert_rules ORDER BY severity DESC, id"
        )

    result: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["condition"] = json.loads(item.get("condition", "{}") or "{}")
            item["notifyChannels"] = json.loads(item.get("notifyChannels", '["websocket"]') or '["websocket"]')
        except (json.JSONDecodeError, TypeError):
            item["condition"] = {}
            item["notifyChannels"] = ["websocket"]
        result.append(item)

    return result


@router.post("/rules")
async def create_rule(body: dict, user: dict = Depends(get_current_user)) -> dict:
    """Create a new alert rule."""
    require_admin(user)

    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    rule_type = (body.get("ruleType") or "agent_offline").strip()
    if rule_type not in ("agent_offline", "high_failure_rate", "token_overflow", "circuit_breaker", "custom"):
        raise HTTPException(status_code=400, detail="Invalid ruleType")

    severity = (body.get("severity") or "warning").strip().lower()
    if severity not in ("info", "warning", "critical"):
        raise HTTPException(status_code=400, detail="severity must be info, warning, or critical")

    condition = json.dumps(body.get("condition") or {}, ensure_ascii=False)
    description = (body.get("description") or "").strip()
    enabled = 1 if body.get("enabled", True) else 0
    notify_channels = json.dumps(body.get("notifyChannels") or ["websocket"], ensure_ascii=False)
    silence_window = int(body.get("silenceWindowSeconds", 3600))

    rid = await aexecute_insert(
        "INSERT INTO alert_rules (name, description, rule_type, condition_json, "
        "severity, enabled, notify_channels, silence_window_seconds, created_at, updated_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id",
        name, description, rule_type, condition, severity, enabled,
        notify_channels, silence_window, _now(), _now(),
    )

    write_audit(
        user["id"], f"alert/{rid}", "alert_rule_create",
        "L2", "approve",
        {"name": name, "ruleType": rule_type, "severity": severity},
    )

    return {"status": "success", "id": rid, "name": name}


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: int, body: dict, user: dict = Depends(get_current_user)) -> dict:
    """Update an alert rule."""
    require_admin(user)

    row = await afetch_one("SELECT id FROM alert_rules WHERE id = $1", rule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")

    severity = (body.get("severity") or "warning").strip().lower()
    if severity not in ("info", "warning", "critical"):
        raise HTTPException(status_code=400, detail="Invalid severity")

    condition = json.dumps(body.get("condition") or {}, ensure_ascii=False)
    notify_channels = json.dumps(body.get("notifyChannels") or ["websocket"], ensure_ascii=False)
    enabled = 1 if body.get("enabled", True) else 0

    await aexecute(
        "UPDATE alert_rules SET name=$1, description=$2, rule_type=$3, "
        "condition_json=$4, severity=$5, enabled=$6, notify_channels=$7, "
        "silence_window_seconds=$8, updated_at=$9 WHERE id=$10",
        (body.get("name") or "").strip(),
        (body.get("description") or "").strip(),
        (body.get("ruleType") or "agent_offline").strip(),
        condition, severity, enabled, notify_channels,
        int(body.get("silenceWindowSeconds", 3600)),
        _now(), rule_id,
    )

    write_audit(
        user["id"], f"alert/{rule_id}", "alert_rule_update",
        "L2", "approve",
        {"ruleId": rule_id},
    )

    return {"status": "success", "id": rule_id}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Delete an alert rule."""
    require_admin(user)

    row = await afetch_one("SELECT id, name FROM alert_rules WHERE id = $1", rule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")

    await aexecute("DELETE FROM alert_rules WHERE id = $1", rule_id)

    write_audit(
        user["id"], f"alert/{rule_id}", "alert_rule_delete",
        "L2", "approve",
        {"ruleName": row["name"]},
    )

    return {"status": "success", "deleted": rule_id}


# ── Alert History ─────────────────────────────────────────────────────

@router.get("/history")
async def alert_history(
    user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=5, le=200, alias="pageSize"),
    severity: str = Query(""),
    acknowledged: str = Query("", description="Filter: all, yes, no"),
) -> dict:
    """Return paginated alert history."""
    require_admin(user)

    conditions = ["1=1"]
    params: list = []
    idx = 0

    if severity:
        idx += 1
        conditions.append(f"severity = ${idx}")
        params.append(severity)
    if acknowledged == "yes":
        conditions.append("acknowledged = 1")
    elif acknowledged == "no":
        conditions.append("acknowledged = 0")

    where = " AND ".join(conditions)

    count_row = await afetch_one(
        f"SELECT COUNT(*) AS cnt FROM alert_history WHERE {where}", *params,
    )
    total = int(count_row["cnt"]) if count_row else 0

    offset = (page - 1) * page_size
    rows = await afetch_all(
        f"SELECT id, rule_id AS \"ruleId\", rule_name AS \"ruleName\", severity, "
        f"message, context_json AS context, acknowledged, acknowledged_by AS \"acknowledgedBy\", "
        f"triggered_at AS \"triggeredAt\", resolved_at AS \"resolvedAt\" "
        f"FROM alert_history WHERE {where} "
        f"ORDER BY triggered_at DESC LIMIT {page_size} OFFSET {offset}",
        *params,
    )

    items: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["context"] = json.loads(item.get("context", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            item["context"] = {}
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": max(1, (total + page_size - 1) // page_size) if total > 0 else 0,
    }


@router.post("/history/{alert_id}/ack")
async def acknowledge_alert(
    alert_id: str,
    body: dict | None = None,
    user: dict = Depends(get_current_user),
) -> dict:
    """Acknowledge (or silence) an alert."""
    require_admin(user)

    row = await afetch_one("SELECT id FROM alert_history WHERE id = $1", alert_id)
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")

    await aexecute(
        "UPDATE alert_history SET acknowledged = 1, acknowledged_by = $1, "
        "resolved_at = $2 WHERE id = $3",
        user["id"], _now(), alert_id,
    )

    write_audit(
        user["id"], alert_id, "alert_acknowledge",
        "L1", "approve",
        {"alertId": alert_id},
    )

    return {"status": "success", "alertId": alert_id, "acknowledged": True}


# ── Manual evaluation trigger ─────────────────────────────────────────

@router.post("/evaluate")
async def evaluate_alerts(body: dict | None = None, user: dict = Depends(get_current_user)) -> dict:
    """Manually trigger alert rule evaluation.

    Body (optional):
        {"ruleId": 1}  — evaluate a single rule; omit for all enabled rules
    """
    require_admin(user)

    rule_id = (body or {}).get("ruleId")

    if rule_id:
        rules = await afetch_all(
            "SELECT * FROM alert_rules WHERE id = $1 AND enabled = 1", rule_id,
        )
    else:
        rules = await afetch_all(
            "SELECT * FROM alert_rules WHERE enabled = 1 ORDER BY id",
        )

    triggered = []
    for rule in rules:
        try:
            triggered_alerts = await _evaluate_single_rule(dict(rule))
            triggered.extend(triggered_alerts)
        except Exception:
            pass

    return {
        "status": "success",
        "rulesEvaluated": len(rules),
        "alertsTriggered": len(triggered),
        "alerts": triggered,
    }


async def _evaluate_single_rule(rule: dict) -> list[dict]:
    """Evaluate a single alert rule and create alert history entries if conditions met."""
    from datetime import datetime

    rule_type = rule["rule_type"]
    triggered: list[dict] = []

    try:
        condition = json.loads(rule.get("condition_json", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        condition = {}

    threshold = float(condition.get("threshold", 0.1))
    duration_seconds = int(condition.get("duration_seconds", 300))

    alert_id = str(uuid.uuid4())
    now_ts = _now()

    if rule_type == "agent_offline":
        # Check how many agents are offline
        offline_row = await afetch_one(
            "SELECT COUNT(*) AS cnt FROM agent_registry WHERE status = 'offline'"
        )
        offline_count = int(offline_row["cnt"]) if offline_row else 0
        if offline_count > int(threshold):
            alert_id = str(uuid.uuid4())
            await aexecute(
                "INSERT INTO alert_history (id, rule_id, rule_name, severity, message, "
                "context_json, acknowledged, triggered_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                alert_id, rule["id"], rule["name"], rule["severity"],
                f"{offline_count} agents are offline (threshold: {int(threshold)})",
                json.dumps({"offlineCount": offline_count, "threshold": threshold}),
                0, now_ts,
            )
            triggered.append({"id": alert_id, "rule": rule["name"], "message": f"{offline_count} agents offline"})

    elif rule_type == "high_failure_rate":
        # Check recent tool failure rate
        recent_start = (datetime.now() - datetime.timedelta(seconds=duration_seconds)).isoformat(timespec="seconds")
        fail_row = await afetch_one(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),0) AS fails "
            "FROM tool_call_log WHERE created_at >= $1",
            recent_start,
        )
        if fail_row and fail_row["total"] > 5:
            fail_rate = fail_row["fails"] / fail_row["total"]
            if fail_rate > threshold:
                alert_id = str(uuid.uuid4())
                await aexecute(
                    "INSERT INTO alert_history (id, rule_id, rule_name, severity, message, "
                    "context_json, acknowledged, triggered_at) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                    alert_id, rule["id"], rule["name"], rule["severity"],
                    f"Tool failure rate {fail_rate:.1%} exceeds threshold {threshold:.0%}",
                    json.dumps({"failRate": fail_rate, "threshold": threshold,
                                "totalCalls": fail_row["total"], "failures": fail_row["fails"]}),
                    0, now_ts,
                )
                triggered.append({"id": alert_id, "rule": rule["name"], "message": f"Failure rate {fail_rate:.1%}"})

    elif rule_type == "circuit_breaker":
        # Check for recent circuit breaker events in audit log
        breaker_rows = await afetch_all(
            "SELECT COUNT(*) AS cnt FROM audit_log WHERE action = 'circuit_breaker' "
            "AND timestamp >= $1",
            (datetime.now() - datetime.timedelta(seconds=duration_seconds)).isoformat(timespec="seconds"),
        )
        breaker_count = int(breaker_rows[0]["cnt"]) if breaker_rows else 0
        if breaker_count > int(threshold):
            alert_id = str(uuid.uuid4())
            await aexecute(
                "INSERT INTO alert_history (id, rule_id, rule_name, severity, message, "
                "context_json, acknowledged, triggered_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                alert_id, rule["id"], rule["name"], rule["severity"],
                f"{breaker_count} circuit breaker events in the last {duration_seconds}s",
                json.dumps({"circuitBreakerCount": breaker_count, "threshold": threshold}),
                0, now_ts,
            )
            triggered.append({"id": alert_id, "rule": rule["name"], "message": f"{breaker_count} circuit breaker events"})

    return triggered
