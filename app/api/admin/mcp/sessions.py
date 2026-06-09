"""MCP Session Manager — session list, detail, force-close, cleanup.

Endpoints:
  GET    /mcp/sessions             List sessions
  GET    /mcp/sessions/{id}        Session detail
  DELETE /mcp/sessions/{id}        Force-close a session
  POST   /mcp/sessions/cleanup     Batch-cleanup stale sessions
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.session import afetch_all, afetch_one, aexecute
from app.services.auth_service import get_current_user, require_admin, write_audit
from app.services.websocket_manager import manager as ws_manager

router = APIRouter(prefix="/sessions", tags=["admin-mcp-sessions"])


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


@router.get("")
async def list_sessions(
    user: dict = Depends(get_current_user),
    search: str = Query("", description="Search by session_id or name"),
    owner_id: str = Query("", alias="ownerId", description="Filter by owner"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=5, le=200, alias="pageSize"),
) -> dict:
    """Return paginated session list (user-scoped)."""
    require_admin(user)

    conditions = ["1=1"]
    params: list = []
    idx = 0

    if search:
        idx += 1
        conditions.append(f"(id LIKE ${idx} OR name LIKE ${idx})")
        params.append(f"%{search}%")
    if owner_id:
        idx += 1
        conditions.append(f"owner_id = ${idx}")
        params.append(owner_id)

    where = " AND ".join(conditions)

    # Count
    count_row = await afetch_one(
        f"SELECT COUNT(*) AS cnt FROM sessions WHERE {where}", *params,
    )
    total = int(count_row["cnt"]) if count_row else 0

    # Fetch page
    offset = (page - 1) * page_size
    rows = await afetch_all(
        f"SELECT id, name, type, participants, active, is_pinned AS \"isPinned\", "
        f"last_message_at AS \"lastMessageAt\", created_at AS \"createdAt\", "
        f"owner_id AS \"ownerId\", visibility "
        f"FROM sessions WHERE {where} "
        f"ORDER BY last_message_at DESC NULLS LAST, created_at DESC "
        f"LIMIT {page_size} OFFSET {offset}",
        *params,
    )

    # Count active members for each session
    for row in rows:
        sid = row["id"]
        member_row = await afetch_one(
            "SELECT COUNT(*) AS cnt FROM session_members WHERE session_id = $1",
            sid,
        )
        row["memberCount"] = int(member_row["cnt"]) if member_row else 0

        # Parse participants JSON
        try:
            row["participants"] = json.loads(row.get("participants", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            row["participants"] = []

    return {
        "items": list(rows),
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": max(1, (total + page_size - 1) // page_size) if total > 0 else 0,
    }


@router.get("/{session_id}")
async def session_detail(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Return full detail for a single session."""
    require_admin(user)

    row = await afetch_one(
        "SELECT id, name, type, participants, active, is_pinned AS \"isPinned\", "
        "last_message_at AS \"lastMessageAt\", created_at AS \"createdAt\", "
        "owner_id AS \"ownerId\", visibility "
        "FROM sessions WHERE id = $1",
        session_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    # Participant details
    try:
        row["participants"] = json.loads(row.get("participants", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        row["participants"] = []

    # Members
    members = await afetch_all(
        "SELECT sm.user_id AS \"userId\", sm.role, sm.joined_at AS \"joinedAt\", "
        "u.name AS \"userName\" "
        "FROM session_members sm LEFT JOIN users u ON sm.user_id = u.id "
        "WHERE sm.session_id = $1 ORDER BY sm.joined_at",
        session_id,
    )
    row["members"] = [dict(m) for m in members]

    # Message count
    msg_row = await afetch_one(
        "SELECT COUNT(*) AS cnt FROM messages WHERE session_id = $1",
        session_id,
    )
    row["messageCount"] = int(msg_row["cnt"]) if msg_row else 0

    # Active WebSocket connections in this session
    conns = ws_manager.get_connections_for_session(session_id)
    row["activeConnections"] = len(conns) if conns else 0

    return dict(row)


@router.delete("/{session_id}")
async def close_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Force-close a session: disconnect members, mark inactive, broadcast."""
    require_admin(user)

    row = await afetch_one(
        "SELECT id FROM sessions WHERE id = $1",
        session_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    # Broadcast close event to all connected clients in this session
    await ws_manager.broadcast(
        session_id,
        {
            "event": "session_closed",
            "sessionId": session_id,
            "reason": "admin_force_close",
            "timestamp": _now(),
        },
    )

    # Mark session inactive
    await aexecute(
        "UPDATE sessions SET active = 0 WHERE id = $1",
        session_id,
    )

    # Clean up session tokens
    tokens = ws_manager.get_tokens_for_session(session_id)
    for token in tokens:
        if not token.cancelled:
            token.cancel()

    write_audit(
        user["id"], session_id, "session_force_close",
        "L2", "approve",
        {"sessionId": session_id},
    )

    return {"status": "success", "sessionId": session_id, "action": "closed"}


@router.post("/cleanup")
async def cleanup_sessions(
    body: dict,
    user: dict = Depends(get_current_user),
) -> dict:
    """Batch-cleanup stale/inactive sessions and their messages.

    Body:
        {"olderThanDays": 30, "onlyInactive": true, "dryRun": false}
    """
    require_admin(user)

    older_than_days = int(body.get("olderThanDays", 30))
    only_inactive = body.get("onlyInactive", True)
    dry_run = body.get("dryRun", True)

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat(timespec="seconds")

    # Find matching sessions
    if only_inactive:
        rows = await afetch_all(
            "SELECT id FROM sessions WHERE active = 0 AND last_message_at < $1",
            cutoff,
        )
    else:
        rows = await afetch_all(
            "SELECT id FROM sessions WHERE last_message_at < $1",
            cutoff,
        )

    session_ids = [r["id"] for r in rows]

    if not dry_run:
        deleted_msgs = 0
        for sid in session_ids:
            # Delete messages first
            msg_result = await aexecute(
                "DELETE FROM messages WHERE session_id = $1", sid,
            )
            # Delete session members
            await aexecute(
                "DELETE FROM session_members WHERE session_id = $1", sid,
            )
            # Delete the session
            await aexecute(
                "DELETE FROM sessions WHERE id = $1", sid,
            )

        write_audit(
            user["id"], "batch", "session_cleanup",
            "L2", "approve",
            {
                "olderThanDays": older_than_days,
                "onlyInactive": only_inactive,
                "dryRun": False,
                "deletedSessions": len(session_ids),
            },
        )

    return {
        "status": "success",
        "dryRun": dry_run,
        "matchedSessions": len(session_ids),
        "sessionIds": session_ids[:100],  # limit list size
        "cutoffDate": cutoff,
    }
