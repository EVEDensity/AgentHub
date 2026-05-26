from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket


class StreamToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class WebSocketManager:
    def __init__(self) -> None:
        self.clients: dict[str, list[WebSocket]] = {}
        self._stream_tokens: dict[str, StreamToken] = {}

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

    def get_stream_token(self, session_id: str) -> StreamToken:
        token = StreamToken()
        self._stream_tokens[session_id] = token
        return token

    def cancel_stream(self, session_id: str) -> str | None:
        token = self._stream_tokens.pop(session_id, None)
        if token and not token.cancelled:
            token.cancel()
            return session_id
        return None

    def has_active_stream(self, session_id: str) -> bool:
        token = self._stream_tokens.get(session_id)
        return token is not None and not token.cancelled

    async def stream_broadcast(self, session_id: str, message_id: str, chunk: str, is_final: bool = False) -> None:
        payload: dict[str, Any] = {
            "event": "message_chunk",
            "messageId": message_id,
            "sessionId": session_id,
            "content": chunk,
            "isFinal": is_final,
        }
        await self.broadcast(session_id, payload)

    async def send_stream_interrupted(self, session_id: str, reason: str) -> None:
        await self.broadcast(
            session_id,
            {
                "event": "stream_interrupted",
                "sessionId": session_id,
                "reason": reason,
            },
        )


manager = WebSocketManager()
