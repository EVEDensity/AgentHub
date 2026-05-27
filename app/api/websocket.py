from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.db.init_db import now
from app.services.agent_service import save_message
from app.services.auth_service import websocket_user
from app.services.message_router import route_message, stream_message
from app.services.websocket_manager import manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, token: str | None = Query(default=None)) -> None:
    user = websocket_user(token)
    await manager.connect(session_id, websocket)
    try:
        await manager.broadcast(
            session_id,
            {
                "event": "message",
                "type": "system",
                "sender": "system",
                "content": f"已连接 AgentHub 实时通道：{user['name']}",
                "timestamp": now(),
                "sessionId": session_id,
            },
        )
        while True:
            data = await websocket.receive_json()
            content = str(data.get("content", "")).strip()
            if not content:
                continue

            # Cancel any in-flight stream — the per-session lock inside
            # _process_and_stream guarantees the old task releases it
            # before the new one proceeds.
            manager.cancel_token(session_id)
            await asyncio.sleep(0.02)

            asyncio.create_task(
                _process_and_stream(
                    session_id,
                    content,
                    data.get("sender", user["name"]),
                    user["id"],
                    data.get("attachments", []),
                )
            )

    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)
        manager.teardown_session(session_id)


async def _process_and_stream(
    session_id: str,
    content: str,
    sender: str,
    user_id: str,
    attachments: list[dict] | None = None,
) -> None:
    """Process one user message with the per-session lock held.

    Only one instance of this coroutine runs per session at a time.
    The caller is responsible for calling ``manager.cancel_token``
    *before* scheduling this task so that any in-flight work sees the
    cancellation signal and releases the lock promptly.
    """
    lock = manager.get_session_lock(session_id)
    async with lock:
        token = manager.create_token(session_id)

        try:
            # Persist the user message regardless of streaming path
            save_message(session_id, sender, content, "text")

            stream_result = await stream_message(session_id, content, sender, user_id, token, attachments or [])

            # Non-streaming fallback
            if stream_result is None:
                response = await route_message(session_id, content, sender, user_id, attachments or [])
                await manager.broadcast(session_id, response)
                return

            # Streaming path
            message_id = str(uuid.uuid4())

            async for chunk in stream_result:
                if token.cancelled:
                    return
                await manager.stream_broadcast(session_id, message_id, chunk, is_final=False)

            if not token.cancelled:
                await manager.stream_broadcast(session_id, message_id, "", is_final=True)

        except Exception:
            if not token.cancelled:
                await manager.broadcast(
                    session_id,
                    {
                        "event": "message",
                        "sessionId": session_id,
                        "content": "模型调用失败",
                        "sender": "system",
                        "timestamp": now(),
                        "type": "system",
                    },
                )
        finally:
            manager.remove_token(session_id, token)
