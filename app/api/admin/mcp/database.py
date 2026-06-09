"""MCP Database & Storage Management — pool status, table stats, cleanup.

Endpoints:
  GET    /mcp/database/status        DB pool status + table statistics
  POST   /mcp/database/cleanup       Manual data cleanup by retention policy
  POST   /mcp/database/export        Export data as JSON Lines
  POST   /mcp/database/vacuum        Trigger VACUUM ANALYZE
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.db.session import afetch_all, afetch_one, aexecute, aget_pool
from app.services.auth_service import get_current_user, require_admin, write_audit

router = APIRouter(prefix="/database", tags=["admin-mcp-database"])


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


@router.get("/status")
async def database_status(user: dict = Depends(get_current_user)) -> dict:
    """Return database connection pool status and table statistics."""
    require_admin(user)

    pool = await aget_pool()

    # Pool status
    pool_info: dict = {
        "connected": pool is not None,
        "minSize": getattr(pool, "_minsize", 2) if pool else 0,
        "maxSize": getattr(pool, "_maxsize", 20) if pool else 0,
    }

    # Active connections from pg_stat_activity
    active_conns: list[dict] = []
    try:
        pg_rows = await afetch_all(
            "SELECT pid, state, query_start AS \"queryStart\", "
            "LEFT(query, 200) AS query_preview "
            "FROM pg_stat_activity WHERE datname = current_database() "
            "AND pid <> pg_backend_pid() ORDER BY query_start DESC NULLS LAST"
        )
        active_conns = [dict(r) for r in pg_rows]
        pool_info["activeConnections"] = len(pg_rows)
    except Exception:
        pool_info["activeConnections"] = -1  # permission denied or pg unavailable

    # Table row counts (estimates from pg_stat_user_tables)
    table_stats: list[dict] = []
    try:
        table_rows = await afetch_all(
            "SELECT relname AS table_name, n_live_tup AS estimated_rows, "
            "pg_size_pretty(pg_total_relation_size(relid)) AS total_size "
            "FROM pg_stat_user_tables ORDER BY n_live_tup DESC"
        )
        table_stats = [dict(r) for r in table_rows]
    except Exception:
        pass

    # Database size
    db_size_str = "unknown"
    try:
        db_size_row = await afetch_one(
            "SELECT pg_size_pretty(pg_database_size(current_database())) AS size"
        )
        if db_size_row:
            db_size_str = db_size_row["size"]
    except Exception:
        pass

    return {
        "pool": pool_info,
        "databaseSize": db_size_str,
        "tables": table_stats,
        "activeQueries": active_conns,
        "timestamp": _now(),
    }


@router.post("/cleanup")
async def database_cleanup(body: dict, user: dict = Depends(get_current_user)) -> dict:
    """Manually purge old data based on retention policies.

    Body:
        {
            "table": "messages",     // messages, audit_log, tool_call_log
            "olderThanDays": 90,
            "dryRun": true
        }
    """
    require_admin(user)

    table_name = (body.get("table") or "messages").strip().lower()
    if table_name not in ("messages", "audit_log", "tool_call_log"):
        raise HTTPException(status_code=400, detail="table must be: messages, audit_log, tool_call_log")

    older_than_days = int(body.get("olderThanDays", 90))
    dry_run = body.get("dryRun", True)

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat(timespec="seconds")

    # Count matching rows
    count_row = await afetch_one(
        f"SELECT COUNT(*) AS cnt FROM {table_name} WHERE created_at < $1", cutoff,
    )
    total = int(count_row["cnt"]) if count_row else 0

    if not dry_run and total > 0:
        await aexecute(
            f"DELETE FROM {table_name} WHERE created_at < $1", cutoff,
        )

    write_audit(
        user["id"], "database", "db_cleanup",
        "L2" if not dry_run else "L1", "approve",
        {
            "table": table_name,
            "olderThanDays": older_than_days,
            "dryRun": dry_run,
            "deletedRows": total if not dry_run else 0,
        },
    )

    return {
        "status": "success",
        "table": table_name,
        "olderThanDays": older_than_days,
        "dryRun": dry_run,
        "matchedRows": total,
        "deletedRows": total if not dry_run else 0,
        "cutoffDate": cutoff,
    }


@router.post("/export")
async def database_export(body: dict, user: dict = Depends(get_current_user)):
    """Export a table as JSON Lines (streaming response).

    Body:
        {"table": "messages", "sessionId": "session-1", "limit": 10000}
    """
    require_admin(user)

    table_name = (body.get("table") or "messages").strip().lower()
    if table_name not in ("messages", "audit_log", "tool_call_log", "agent_registry"):
        raise HTTPException(status_code=400, detail="table not exportable")

    limit = min(int(body.get("limit", 10000)), 50000)
    session_id = (body.get("sessionId") or "").strip()

    import json

    if session_id and table_name == "messages":
        rows = await afetch_all(
            f"SELECT * FROM {table_name} WHERE session_id = $1 "
            f"ORDER BY created_at LIMIT {limit}",
            session_id,
        )
    else:
        # Safety check: refuse full exports of large tables without session filter
        if table_name in ("messages", "tool_call_log") and not session_id:
            raise HTTPException(
                status_code=400,
                detail="Must provide sessionId filter for messages/tool_call_log exports",
            )
        rows = await afetch_all(
            f"SELECT * FROM {table_name} ORDER BY created_at DESC LIMIT {limit}"
        )

    async def _generate():
        for row in rows:
            yield json.dumps(dict(row), default=str, ensure_ascii=False) + "\n"

    write_audit(
        user["id"], "database", "db_export",
        "L2", "approve",
        {"table": table_name, "sessionId": session_id, "rowsExported": len(rows)},
    )

    filename = f"{table_name}_export_{_now()[:19].replace(':','-')}.jsonl"
    return StreamingResponse(
        _generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/vacuum")
async def database_vacuum(body: dict | None = None, user: dict = Depends(get_current_user)) -> dict:
    """Trigger VACUUM ANALYZE on the database (admin only, DANGER)."""
    require_admin(user)

    table_name = (body or {}).get("table", "").strip()
    try:
        if table_name:
            await aexecute(f"VACUUM ANALYZE {table_name}")
        else:
            await aexecute("VACUUM ANALYZE")
        message = f"VACUUM ANALYZE completed" + (f" on {table_name}" if table_name else " on all tables")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"VACUUM failed: {exc}") from exc

    write_audit(
        user["id"], "database", "db_vacuum",
        "L3", "approve",
        {"table": table_name or "all"},
    )

    return {"status": "success", "message": message}
