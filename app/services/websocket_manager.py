from __future__ import annotations

from typing import Any

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self) -> None:
        self.clients: dict[str, list[WebSocket]] = {}

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.clients.setdefault(session_id, []).append(websocket)

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        clients = self.clients.get(session_id, [])
        if websocket in clients:
            clients.remove(websocket)
        if not clients and session_id in self.clients:
            self.clients.pop(session_id, None)

    async def broadcast(self, session_id: str, payload: dict[str, Any]) -> None:
        for websocket in list(self.clients.get(session_id, [])):
            try:
                await websocket.send_json(payload)
            except RuntimeError:
                self.disconnect(session_id, websocket)


manager = WebSocketManager()
