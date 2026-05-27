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
        self._tokens: dict[str, StreamToken] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ── connection management ──────────────────────────────────────

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

    # ── per-session serialisation lock ─────────────────────────────

    def get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Return (creating if necessary) the per-session mutex that
        guarantees at most one message is processed at a time."""
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    # ── stream-token lifecycle (per-session, task-bound) ──────────

    def create_token(self, session_id: str) -> StreamToken:
        """Create a fresh cancellation token for the session.

        Replaces any previous token – the old token is cancelled so the
        prior task will exit at its next ``token.cancelled`` check."""
        old = self._tokens.pop(session_id, None)
        if old and not old.cancelled:
            old.cancel()
        token = StreamToken()
        self._tokens[session_id] = token
        return token

    def cancel_token(self, session_id: str) -> None:
        """Signal the current stream (if any) to stop."""
        token = self._tokens.get(session_id)
        if token and not token.cancelled:
            token.cancel()

    def remove_token(self, session_id: str, token: StreamToken) -> None:
        """Remove *token* only if it is still the registered one."""
        current = self._tokens.get(session_id)
        if current is token:
            self._tokens.pop(session_id, None)

    def has_active_stream(self, session_id: str) -> bool:
        token = self._tokens.get(session_id)
        return token is not None and not token.cancelled

    # ── streaming broadcast helpers ────────────────────────────────

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

    # ── session cleanup ────────────────────────────────────────────

    def teardown_session(self, session_id: str) -> None:
        """Cancel any in-flight work and release session-scoped resources."""
        self.cancel_token(session_id)
        self._tokens.pop(session_id, None)
        self._locks.pop(session_id, None)
        self.clients.pop(session_id, None)


manager = WebSocketManager()
