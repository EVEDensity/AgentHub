from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.db.init_db import now
from app.services.auth_service import websocket_user
from app.services.message_router import route_message
from app.services.websocket_manager import manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, token: str | None = Query(default=None)) -> None:
    user = websocket_user(token)
    await manager.connect(session_id, websocket)
    try:
        await manager.broadcast(session_id, {"event": "message", "type": "system", "sender": "system", "content": f"已连接 AgentHub 实时通道：{user['name']}", "timestamp": now(), "sessionId": session_id})
        while True:
            data = await websocket.receive_json()
            content = str(data.get("content", "")).strip()
            if not content:
                continue
            response = await route_message(session_id, content, data.get("sender", user["name"]), user_id=user["id"])
            await manager.broadcast(session_id, response)
    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)
