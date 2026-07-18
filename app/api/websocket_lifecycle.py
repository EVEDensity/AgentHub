from __future__ import annotations

import asyncio
from typing import Any

from app.db.init_db import now


def _manager():
    from app.services.websocket_manager import manager as _manager_instance

    return _manager_instance


async def open_websocket_session(
    *,
    session_id: str,
    websocket: Any,
    user_id: str,
    user_name: str,
    role: str,
) -> tuple[str, asyncio.Task]:
    ws_manager = _manager()

    conn_id = await ws_manager.connect(session_id, websocket, user_id, role, user_name)
    await ws_manager.broadcast_user_event(session_id, {
        "event": "user_joined",
        "sessionId": session_id,
        "userId": user_id,
        "userName": user_name,
        "role": role,
        "timestamp": now(),
    }, exclude_user=user_id)
    await ws_manager._send_safe(websocket, {
        "event": "user_roster",
        "sessionId": session_id,
        "users": ws_manager.get_online_users(session_id),
    })
    heartbeat_task = asyncio.create_task(
        ws_manager.heartbeat_loop(session_id, conn_id, websocket),
    )
    return conn_id, heartbeat_task


async def close_websocket_session(
    *,
    session_id: str,
    websocket: Any,
    user_id: str,
    user_name: str,
    heartbeat_task: asyncio.Task,
) -> None:
    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except (asyncio.CancelledError, Exception):
        pass

    try:
        await ws_manager.broadcast_user_event(session_id, {
            "event": "user_left",
            "sessionId": session_id,
            "userId": user_id,
            "userName": user_name,
            "timestamp": now(),
        }, exclude_user=user_id)
    except Exception:
        pass

    ws_manager.disconnect(session_id, websocket)
    if ws_manager._connection_count(session_id) == 0:
        ws_manager.teardown_session(session_id)
