from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.db.init_db import now
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

            if manager.has_active_stream(session_id):
                stream_id = manager.cancel_stream(session_id)
                if stream_id:
                    await manager.broadcast(
                        session_id,
                        {
                            "event": "stream_interrupted",
                            "sessionId": session_id,
                            "reason": "user_interaction",
                            "timestamp": now(),
                        },
                    )
                    await asyncio.sleep(0.05)

            task = asyncio.create_task(
                _process_and_stream(session_id, content, data.get("sender", user["name"]), user["id"])
            )

    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)
        manager.cancel_stream(session_id)


async def _process_and_stream(session_id: str, content: str, sender: str, user_id: str) -> None:
    token = manager.get_stream_token(session_id)

    try:
        stream_result = await stream_message(session_id, content, sender, user_id, token)

        if stream_result is None:
            response = await route_message(session_id, content, sender, user_id)
            await manager.broadcast(session_id, response)
            return

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
        manager._stream_tokens.pop(session_id, None)
