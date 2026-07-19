from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.api import websocket_state as ws_state
from app.db.init_db import now

ControlCallback = Callable[[str, dict, str, str], Awaitable[None]]
DiffCallback = Callable[[str, dict], Awaitable[None]]
ProcessCallback = Callable[[str, str, str, str, list[dict], list[dict], bool], Awaitable[None]]


def _manager():
    from app.services.websocket_manager import manager as _manager_instance

    return _manager_instance


async def dispatch_control_event(
    *,
    session_id: str,
    data: dict,
    websocket: Any,
    user_id: str,
    user_name: str,
    conn_id: str,
    on_agent_question_response: ControlCallback,
    on_risk_warning_response: ControlCallback,
    on_agent_todo_response: ControlCallback,
    on_task_preview_response: ControlCallback,
    on_solution_selection: ControlCallback,
    on_diff_decision: DiffCallback,
) -> bool:
    event_name = data.get("event")
    ws_manager = _manager()

    if event_name == "pong":
        ws_manager.record_pong(session_id, conn_id)
        return True

    if event_name == "set_presence":
        status = data.get("status", "online")
        ws_manager.set_user_presence(session_id, user_id, status)
        await ws_manager.broadcast_user_event(session_id, {
            "event": "presence_update",
            "sessionId": session_id,
            "users": [{"userId": user_id, "status": status}],
        }, exclude_user=user_id)
        return True

    if event_name == "typing":
        is_typing = data.get("isTyping", False)
        await ws_manager.broadcast_user_event(session_id, {
            "event": "typing_indicator",
            "sessionId": session_id,
            "userId": user_id,
            "userName": user_name,
            "isTyping": is_typing,
        }, exclude_user=user_id)
        return True

    if event_name == "sync_request":
        last_id = data.get("lastMessageId")
        count = await ws_manager.replay_missed_messages(session_id, websocket, last_id)
        await ws_manager._send_safe(websocket, {
            "event": "sync_complete",
            "sessionId": session_id,
            "replayed": count,
        })
        return True

    if event_name == "permission_response":
        request_id = data.get("requestId", "")
        decision = data.get("decision", "deny")
        if request_id:
            ws_state.handle_permission_response(session_id, request_id, decision)
        return True

    if event_name == "agent_question_response":
        await on_agent_question_response(session_id, data, user_id, user_name)
        return True

    if event_name == "risk_warning_response":
        await on_risk_warning_response(session_id, data, user_id, user_name)
        return True

    if event_name == "agent_todo_response":
        await on_agent_todo_response(session_id, data, user_id, user_name)
        return True

    if event_name == "task_preview_response":
        await on_task_preview_response(session_id, data, user_id, user_name)
        return True

    if event_name == "solution_selection":
        await on_solution_selection(session_id, data, user_id, user_name)
        return True

    if event_name == "diff_decision":
        await on_diff_decision(session_id, data)
        return True

    if event_name == "set_exec_permission":
        exec_perm = data.get("mode")
        if isinstance(exec_perm, int) and exec_perm in (1, 2, 3):
            ws_state.set_session_exec_permission(session_id, exec_perm)
            from app.services.tools.permission import set_exec_permission
            set_exec_permission(session_id, exec_perm)
            await ws_manager.broadcast_permission_mode_changed(
                session_id, exec_perm, user_id, user_name,
            )
        return True

    return False


async def dispatch_message_flow(
    *,
    session_id: str,
    content: str,
    sender: str,
    user_id: str,
    access_can_write: bool,
    websocket: Any,
    data: dict,
    attachments: list[dict],
    quote_references: list[dict],
    auto_reply: bool,
    process_and_stream: ProcessCallback,
    log_task_error: Callable[[str, object], None],
) -> bool:
    ws_manager = _manager()

    if not content:
        return False

    if not access_can_write:
        await ws_manager._send_safe(websocket, {
            "event": "system",
            "sessionId": session_id,
            "content": "You do not have permission to send messages in this session.",
            "timestamp": now(),
        })
        return True

    exec_perm = data.get("exec_permission")
    if isinstance(exec_perm, int) and exec_perm in (1, 2, 3):
        ws_state.set_session_exec_permission(session_id, exec_perm)
        from app.services.tools.permission import set_exec_permission
        set_exec_permission(session_id, exec_perm)

    if ws_manager.has_active_stream(session_id):
        ws_manager.cancel_token(session_id)
        await ws_manager.send_stream_interrupted(session_id, "New message received, interrupting current stream")
        lock = ws_manager.get_session_lock(session_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=2.0)
            lock.release()
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(0.02)

    task = asyncio.create_task(
        process_and_stream(
            session_id, content, sender, user_id,
            attachments, quote_references, auto_reply,
        )
    )
    task.add_done_callback(
        lambda t: log_task_error(session_id, t) if t.exception() else None
    )
    return True
