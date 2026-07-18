from __future__ import annotations

import asyncio
import logging
import time

from app.db.init_db import now

logger = logging.getLogger("agenthub.websocket")

# Session-scoped websocket state
_throttle_state: dict[str, float] = {}
_THROTTLE_SECONDS = 30

_permission_state: dict[str, dict[str, dict]] = {}
_session_exec_permission: dict[str, int] = {}

_solution_selection_events: dict[str, asyncio.Event] = {}
_solution_selection_results: dict[str, dict] = {}

_auto_name_state: dict[str, tuple[float, int]] = {}
_AUTO_NAME_INITIAL_SECONDS = 15
_AUTO_NAME_BACKOFF_SECONDS = 120
_AUTO_NAME_MAX_ATTEMPTS = 5

_pm_pending_questions: dict[str, dict[str, dict]] = {}
_pm_pending_warnings: dict[str, dict[str, dict]] = {}
_pm_pending_todos: dict[str, dict[str, dict]] = {}

_pending_task_previews: dict[str, dict[str, dict]] = {}
_resolved_interactions: dict[str, dict[str, dict]] = {}


def get_session_exec_permission(session_id: str) -> int:
    return _session_exec_permission.get(session_id, 1)


def set_session_exec_permission(session_id: str, mode: int) -> None:
    if mode in (1, 2, 3):
        _session_exec_permission[session_id] = mode


def _should_auto_name(session_id: str) -> bool:
    now_ts = time.monotonic()
    last_ts, attempts = _auto_name_state.get(session_id, (0.0, 0))
    if attempts >= _AUTO_NAME_MAX_ATTEMPTS:
        return False
    if attempts == 0:
        _auto_name_state[session_id] = (now_ts, 1)
        return True
    interval = _AUTO_NAME_INITIAL_SECONDS if attempts < 2 else _AUTO_NAME_BACKOFF_SECONDS
    if now_ts - last_ts >= interval:
        _auto_name_state[session_id] = (now_ts, attempts + 1)
        return True
    return False


def handle_permission_response(session_id: str, request_id: str, decision: str) -> bool:
    session_entries = _permission_state.get(session_id, {})
    entry = session_entries.get(request_id)
    if entry:
        entry["decision"] = decision
        entry["event"].set()
        return True
    return False


async def wait_for_task_confirmation(
    session_id: str, preview_msg_id: str, token, user_id: str = "",
) -> tuple[str, str]:
    event = asyncio.Event()
    _pending_task_previews.setdefault(session_id, {})[preview_msg_id] = {
        "event": event,
        "decision": "confirm",
        "modifications": "",
        "owner_id": user_id,
        "member_votes": {} if user_id else None,
    }

    try:
        await asyncio.wait_for(event.wait(), timeout=300)
    except asyncio.TimeoutError:
        _pending_task_previews.get(session_id, {}).pop(preview_msg_id, None)
        logger.info(
            "task_preview_wait timeout session=%s preview=%s - proceeding with execution",
            session_id, preview_msg_id,
        )
        return "confirm", ""

    if token and token.cancelled:
        return "cancel", ""

    entry = _pending_task_previews.get(session_id, {}).pop(preview_msg_id, None)
    if entry:
        return entry["decision"], entry.get("modifications", "")
    return "confirm", ""


def resolve_pending_task_preview(
    session_id: str, preview_msg_id: str, decision: str, modifications: str = "",
) -> bool:
    entry = _pending_task_previews.get(session_id, {}).get(preview_msg_id)
    if entry:
        entry["decision"] = decision
        entry["modifications"] = modifications
        entry["event"].set()
        return True
    return False


def mark_interaction_resolved(session_id: str, message_id: str, user_id: str, user_name: str) -> bool:
    session_map = _resolved_interactions.setdefault(session_id, {})
    if message_id in session_map:
        return False
    session_map[message_id] = {"resolvedBy": user_id, "userName": user_name, "timestamp": now()}
    return True


def get_resolved_by(session_id: str, message_id: str) -> dict | None:
    return _resolved_interactions.get(session_id, {}).get(message_id)


def should_run_memory_tasks(session_id: str) -> bool:
    now_ts = time.monotonic()
    last = _throttle_state.get(session_id, 0.0)
    if now_ts - last >= _THROTTLE_SECONDS:
        _throttle_state[session_id] = now_ts
        return True
    return False


def chunk_text_for_streaming(text: str, chunk_size: int = 60) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    buf = ""
    separators = "，。！？；:!?\n"
    for ch in text:
        buf += ch
        if len(buf) >= chunk_size or ch in separators:
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks
