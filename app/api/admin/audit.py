"""Audit trail — security event logging and retrieval.

Endpoints:
  GET    /audit/logs     List recent audit entries (last 200)
  POST   /audit/entries  Record a new audit entry
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.db.session import dict_rows
from app.schemas.common import AuditConfirmRequest
from app.services.auth_service import get_current_user, require_admin, write_audit

router = APIRouter(prefix="/audit", tags=["admin-audit"])


@router.get("/logs")
async def list_logs(user: dict = Depends(get_current_user)) -> list[dict]:
    """Return the most recent 200 audit-log entries (admin only)."""
    require_admin(user)
    return dict_rows(
        "SELECT id, user_id AS userId, agent_id AS agentId, action, risk_level AS riskLevel, "
        "decision, content_hash AS contentHash, payload_json AS payload, timestamp "
        "FROM audit_log ORDER BY timestamp DESC LIMIT 200"
    )


@router.post("/entries")
async def create_entry(data: AuditConfirmRequest, user: dict = Depends(get_current_user)) -> dict:
    """Append a new entry to the audit log."""
    audit_id = write_audit(
        user["id"], data.agentId, data.action, data.riskLevel, data.decision, data.payload,
    )
    return {"status": "success", "auditId": audit_id}
