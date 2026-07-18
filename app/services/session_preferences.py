from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

PINNED_SESSIONS_SETTING_KEY = "pinned_sessions"


def parse_pinned_session_ids(raw_value: str | None) -> set[str]:
    """Parse a JSON list of session IDs stored in user_settings."""
    if not raw_value:
        return set()
    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item) for item in data if str(item).strip()}


def serialize_pinned_session_ids(session_ids: Iterable[str]) -> str:
    """Serialize pinned session IDs as a stable JSON array string."""
    values = sorted({sid.strip() for sid in session_ids if sid and sid.strip()})
    return json.dumps(values, ensure_ascii=False)


def apply_session_pin_state(
    sessions: list[dict],
    pinned_ids: set[str],
) -> list[dict]:
    """Annotate sessions with per-user pin state and sort pinned first."""
    annotated = []
    for session in sessions:
        item = dict(session)
        item["isPinned"] = 1 if item.get("id") in pinned_ids else 0
        annotated.append(item)

    def _sort_key(item: dict) -> tuple[int, float]:
        ts = item.get("lastMessageAt") or item.get("createdAt") or ""
        try:
            stamp = datetime.fromisoformat(ts).timestamp()
        except (TypeError, ValueError):
            stamp = 0.0
        return (0 if item.get("isPinned") else 1, -stamp)

    return sorted(annotated, key=_sort_key)
