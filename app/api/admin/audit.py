"""Audit trail — security event logging and retrieval.

Endpoints:
  GET    /audit/logs          List audit entries with pagination, sort, filter
  GET    /audit/logs/{id}     Get a single audit entry detail
  POST   /audit/entries       Record a new audit entry
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.session import dict_rows, one_row
from app.schemas.common import AuditConfirmRequest
from app.services.auth_service import get_current_user, require_admin, write_audit

router = APIRouter(prefix="/audit", tags=["admin-audit"])

# ── Valid sort columns ──────────────────────────────────────────────────────

SORT_COLUMNS = {
    "timestamp", "userId", "agentId", "action", "riskLevel", "decision",
}


# ── LIST (paginated, sortable, filterable) ──────────────────────────────────


@router.get("/logs")
async def list_logs(
    user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(25, ge=5, le=200, alias="pageSize", description="Rows per page"),
    sort_by: str = Query("timestamp", alias="sortBy", description="Column to sort by"),
    sort_order: str = Query("desc", alias="sortOrder", description="asc or desc"),
    user_id: str = Query("", alias="userId", description="Filter by user ID"),
    agent_id: str = Query("", alias="agentId", description="Filter by agent ID"),
    action: str = Query("", description="Filter by action"),
    risk_level: str = Query("", alias="riskLevel", description="Filter by risk level"),
    search: str = Query("", description="Free-text search across action, userId, agentId"),
) -> dict:
    """Return paginated audit-log entries (admin only)."""
    require_admin(user)

    # Validate sort column
    sort_col = sort_by if sort_by in SORT_COLUMNS else "timestamp"
    order = "ASC" if sort_order.lower() == "asc" else "DESC"

    # Build WHERE clause
    conditions: list[str] = []
    params: list[str] = []

    if user_id:
        conditions.append("user_id LIKE ?")
        params.append(f"%{user_id}%")
    if agent_id:
        conditions.append("agent_id LIKE ?")
        params.append(f"%{agent_id}%")
    if action:
        conditions.append("action LIKE ?")
        params.append(f"%{action}%")
    if risk_level:
        conditions.append("risk_level = ?")
        params.append(risk_level)
    if search:
        conditions.append(
            "(action LIKE ? OR user_id LIKE ? OR agent_id LIKE ? OR payload_json LIKE ?)"
        )
        q = f"%{search}%"
        params.extend([q, q, q, q])

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    # Count total matching rows
    count_sql = f"SELECT COUNT(*) AS cnt FROM audit_log {where_clause}"
    total_row = one_row(count_sql, tuple(params))
    total = total_row["cnt"] if total_row else 0

    # Map sort column to DB column name
    column_map = {
        "timestamp": "timestamp",
        "userId": "user_id",
        "agentId": "agent_id",
        "action": "action",
        "riskLevel": "risk_level",
        "decision": "decision",
    }
    db_sort_col = column_map.get(sort_col, "timestamp")

    offset = (page - 1) * page_size
    data_sql = (
        f"SELECT id, user_id AS userId, agent_id AS agentId, action, "
        f"risk_level AS riskLevel, decision, content_hash AS contentHash, "
        f"payload_json AS payload, timestamp "
        f"FROM audit_log {where_clause} "
        f"ORDER BY {db_sort_col} {order} "
        f"LIMIT ? OFFSET ?"
    )
    rows = dict_rows(data_sql, tuple(params) + (page_size, offset))

    return {
        "items": rows,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": max(1, (total + page_size - 1) // page_size) if total > 0 else 0,
    }


# ── DETAIL ──────────────────────────────────────────────────────────────────


@router.get("/logs/{log_id}")
async def get_log_detail(
    log_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Return full detail of a single audit-log entry (admin only)."""
    require_admin(user)

    row = one_row(
        "SELECT id, user_id AS userId, agent_id AS agentId, action, "
        "risk_level AS riskLevel, decision, content_hash AS contentHash, "
        "payload_json AS payload, timestamp "
        "FROM audit_log WHERE id = ?",
        (log_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Audit log entry not found")

    return row


# ── CREATE ──────────────────────────────────────────────────────────────────


@router.post("/entries")
async def create_entry(
    data: AuditConfirmRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Append a new entry to the audit log."""
    audit_id = write_audit(
        user["id"], data.agentId, data.action, data.riskLevel, data.decision, data.payload,
    )
    return {"status": "success", "auditId": audit_id}
