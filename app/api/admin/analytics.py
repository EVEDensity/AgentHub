"""Analytics — token usage statistics and heatmap data.

Endpoints:
  GET    /analytics/token-usage   365-day token consumption heatmap
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends

from app.db.session import dict_rows, get_connection
from app.services.auth_service import get_current_user, require_admin

router = APIRouter(prefix="/analytics", tags=["admin-analytics"])


@router.get("/token-usage")
async def token_usage_heatmap(user: dict = Depends(get_current_user)) -> dict:
    """Aggregate 365 days of token usage grouped by day.

    Returns daily sessions, messages, and token counts plus summary
    statistics for today, yesterday, and the trailing 30 days.
    """
    require_admin(user)

    with get_connection() as conn:
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}

    has_total_tokens = "total_tokens" in existing_cols
    has_prompt_tokens = "prompt_tokens" in existing_cols
    has_completion_tokens = "completion_tokens" in existing_cols

    end_day = date.today()
    start_day = end_day - timedelta(days=364)

    if has_total_tokens:
        rows = dict_rows(
            "SELECT substr(created_at, 1, 10) AS day, session_id AS sessionId, content, "
            "total_tokens, prompt_tokens, completion_tokens "
            "FROM messages WHERE created_at >= ? AND created_at <= ? ORDER BY created_at ASC",
            (f"{start_day.isoformat()}T00:00:00", f"{end_day.isoformat()}T23:59:59"),
        )
    else:
        rows = dict_rows(
            "SELECT substr(created_at, 1, 10) AS day, session_id AS sessionId, content "
            "FROM messages WHERE created_at >= ? AND created_at <= ? ORDER BY created_at ASC",
            (f"{start_day.isoformat()}T00:00:00", f"{end_day.isoformat()}T23:59:59"),
        )

    # Aggregate per-day stats
    day_map: dict[str, dict] = {}
    for row in rows:
        day = row.get("day")
        if not day:
            continue
        item = day_map.setdefault(day, {"sessionIds": set(), "tokens": 0, "messages": 0})
        sid = row.get("sessionId") or ""
        if sid:
            item["sessionIds"].add(sid)
        item["messages"] += 1
        if has_total_tokens:
            tk = int(row.get("total_tokens") or 0)
            if tk <= 0 and has_prompt_tokens and has_completion_tokens:
                tk = int(row.get("prompt_tokens") or 0) + int(row.get("completion_tokens") or 0)
            if tk <= 0:
                tk = max(1, int(len(row.get("content") or "") / 4))
            item["tokens"] += tk
        else:
            item["tokens"] += max(1, int(len(row.get("content") or "") / 4))

    days: list[dict] = []
    cursor = start_day
    while cursor <= end_day:
        key = cursor.isoformat()
        data = day_map.get(key, {"sessionIds": set(), "tokens": 0, "messages": 0})
        days.append({
            "date": key,
            "sessions": len(data["sessionIds"]),
            "messages": data["messages"],
            "tokens": int(data["tokens"]),
        })
        cursor += timedelta(days=1)

    def _snapshot(day_key: str) -> dict:
        d = day_map.get(day_key, {"sessionIds": set(), "tokens": 0, "messages": 0})
        return {"sessions": len(d["sessionIds"]), "messages": d["messages"], "tokens": int(d["tokens"])}

    last_30 = end_day - timedelta(days=29)
    last_30_sessions = last_30_messages = last_30_tokens = 0
    c = last_30
    while c <= end_day:
        s = _snapshot(c.isoformat())
        last_30_sessions += s["sessions"]
        last_30_messages += s["messages"]
        last_30_tokens += s["tokens"]
        c += timedelta(days=1)

    return {
        "range": {"start": start_day.isoformat(), "end": end_day.isoformat()},
        "today": _snapshot(end_day.isoformat()),
        "yesterday": _snapshot((end_day - timedelta(days=1)).isoformat()),
        "last30": {
            "sessions": last_30_sessions,
            "messages": last_30_messages,
            "tokens": last_30_tokens,
        },
        "days": days,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
