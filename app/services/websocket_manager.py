from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.db.init_db import now as db_now

logger = logging.getLogger("agenthub.websocket")

# ── Constants ────────────────────────────────────────────────────────
HEARTBEAT_INTERVAL = 25       # seconds between server → client pings
HEARTBEAT_TIMEOUT = 60        # seconds without pong before considering dead
MESSAGE_QUEUE_SIZE = 200      # max recent messages kept per session for recovery
MAX_BACKLOG_MESSAGES = 500    # per-session cap before degrading to summary
BROADCAST_SEND_TIMEOUT = 5.0  # seconds per websocket.send_json (prevents slow-client stall)


class StreamToken:
    """Lightweight cancellation token bound to a session."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class WebSocketManager:
    """Central WebSocket connection manager with stability features.

    - Per-connection IDs for multi-tab support
    - Heartbeat / keep-alive monitoring
    - Safe send that never raises on connection errors
    - Per-session message ring-buffer for reconnection recovery
    - Backpressure-aware streaming
    """

    def __init__(self) -> None:
        # session_id → list of (connection_id, websocket, user_id, connected_at)
        self._connections: dict[str, list[tuple[str, WebSocket, str, float]]] = {}
        # (session_id, connection_id) → last_pong timestamp
        self._heartbeats: dict[tuple[str, str], float] = {}
        # session_id → deque of recent message payloads (for reconnection catch-up)
        self._recent_messages: dict[str, deque[dict[str, Any]]] = {}
        # session_id → StreamToken (cancellation per session, not per connection)
        self._tokens: dict[str, StreamToken] = {}
        # session_id → asyncio.Lock (message processing serialisation)
        self._locks: dict[str, asyncio.Lock] = {}

    # ── Connection lifecycle ─────────────────────────────────────────

    async def connect(self, session_id: str, websocket: WebSocket, user_id: str = "") -> str:
        """Accept the websocket and register the connection.  Returns a unique connection_id."""
        await websocket.accept()
        conn_id = str(uuid.uuid4())
        now_ts = time.monotonic()
        self._connections.setdefault(session_id, []).append((conn_id, websocket, user_id, now_ts))
        self._heartbeats[(session_id, conn_id)] = now_ts
        logger.info("ws connect session=%s conn=%s user=%s", session_id, conn_id, user_id)
        return conn_id

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        """Remove a specific websocket from a session.

        Idempotent — safe to call multiple times for the same connection
        (e.g. after a heartbeat timeout has already pruned it).
        """
        conns = self._connections.get(session_id, [])
        # Build the target tuple (only the ws identity matters for the
        # ``is`` check, but we need a full tuple for ``list.remove``).
        target: tuple[str, WebSocket, str, float] | None = None
        for item in conns:
            if item[1] is websocket:
                target = item
                break
        if target is None:
            return  # already removed (e.g. by broadcast dead-connection pruning)
        try:
            conns.remove(target)
        except ValueError:
            pass  # race with another cleanup path
        self._heartbeats.pop((session_id, target[0]), None)
        logger.info("ws disconnect session=%s conn=%s", session_id, target[0])
        if not conns and session_id in self._connections:
            self._connections.pop(session_id, None)

    def _connection_count(self, session_id: str) -> int:
        return len(self._connections.get(session_id, []))

    # ── Safe send — never raises on connection errors ───────────────

    async def _send_safe(self, websocket: WebSocket, payload: dict[str, Any]) -> bool:
        """Send JSON to a single websocket.  Returns True on success, False if the
        connection is dead (caller should disconnect it)."""
        try:
            if websocket.client_state != WebSocketState.CONNECTED:
                return False
            await websocket.send_json(payload)
            return True
        except (WebSocketDisconnect, RuntimeError, ConnectionError, OSError):
            return False

    # ── Broadcast ────────────────────────────────────────────────────

    async def broadcast(self, session_id: str, payload: dict[str, Any]) -> None:
        """Send a payload to every connection in *session_id*.

        All connections are sent **concurrently** via ``asyncio.gather`` so
        one slow or dead client cannot block others.  A per-send timeout
        (BROADCAST_SEND_TIMEOUT) prevents stalled TCP buffers from holding
        the gather forever.  Dead connections are pruned after the sends
        complete.

        Payloads are also pushed into the per-session ring buffer for
        reconnection catch-up.
        """
        self._push_recent(session_id, payload)
        conns = self._connections.get(session_id, [])
        if not conns:
            return

        # ── Parallel send to all connections ──────────────────────────
        broadcast_start = time.monotonic()

        async def _send_one(cid: str, ws: WebSocket, uid: str, ts: float) -> tuple[str, bool]:
            """Send to one connection with a timeout.  Returns (cid, alive)."""
            try:
                ok = await asyncio.wait_for(
                    self._send_safe(ws, payload),
                    timeout=BROADCAST_SEND_TIMEOUT,
                )
                return (cid, ok)
            except asyncio.TimeoutError:
                logger.warning("ws broadcast timeout session=%s conn=%s", session_id, cid)
                return (cid, False)
            except Exception:
                return (cid, False)

        results = await asyncio.gather(
            *[_send_one(cid, ws, uid, ts) for cid, ws, uid, ts in conns],
            return_exceptions=True,
        )

        broadcast_elapsed = (time.monotonic() - broadcast_start) * 1000

        # ── Prune dead connections ────────────────────────────────────
        dead_cids: set[str] = set()
        for result in results:
            if isinstance(result, tuple) and not result[1]:
                dead_cids.add(result[0])
            elif isinstance(result, Exception):
                logger.warning("ws broadcast unexpected error session=%s: %s", session_id, result)

        if dead_cids:
            surviving: list[tuple[str, WebSocket, str, float]] = []
            for item in conns:
                if item[0] in dead_cids:
                    self._heartbeats.pop((session_id, item[0]), None)
                    logger.warning("ws dead connection pruned session=%s conn=%s", session_id, item[0])
                else:
                    surviving.append(item)
            if surviving:
                self._connections[session_id] = surviving
            else:
                self._connections.pop(session_id, None)

        # ── Record broadcast performance ─────────────────────────────
        ok_count = len(conns) - len(dead_cids)
        try:
            from app.services.performance_monitor import monitor
            monitor.record_broadcast(
                session_id, len(conns), ok_count,
                broadcast_elapsed, 0,
            )
        except Exception:
            pass

    # ── Streaming helpers ────────────────────────────────────────────

    async def stream_broadcast(
        self, session_id: str, message_id: str, chunk: str,
        is_final: bool = False, sender: str = "",
    ) -> None:
        """Broadcast a streaming chunk."""
        payload: dict[str, Any] = {
            "event": "message_chunk",
            "messageId": message_id,
            "sessionId": session_id,
            "content": chunk,
            "isFinal": is_final,
        }
        if sender:
            payload["sender"] = sender
        await self.broadcast(session_id, payload)

    async def send_stream_interrupted(self, session_id: str, reason: str) -> None:
        await self.broadcast(
            session_id,
            {"event": "stream_interrupted", "sessionId": session_id, "reason": reason},
        )

    # ── Heartbeat (application-level ping/pong) ──────────────────────

    async def heartbeat_loop(self, session_id: str, conn_id: str, websocket: WebSocket) -> None:
        """Per-connection background task: periodically ping the client and
        detect zombie connections via missing pongs."""
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                key = (session_id, conn_id)
                if key not in self._heartbeats:
                    return  # connection already removed

                ok = await self._send_safe(websocket, {"event": "ping", "ts": time.time()})
                if not ok:
                    return

                # Check if the client has responded to any recent pings
                last = self._heartbeats.get(key, 0)
                if time.monotonic() - last > HEARTBEAT_TIMEOUT:
                    logger.warning("ws heartbeat timeout session=%s conn=%s", session_id, conn_id)
                    return
        except asyncio.CancelledError:
            pass

    def record_pong(self, session_id: str, conn_id: str) -> None:
        """Called when the client responds to a ping."""
        self._heartbeats[(session_id, conn_id)] = time.monotonic()

    # ── Reconnection message queue ───────────────────────────────────

    def _push_recent(self, session_id: str, payload: dict[str, Any]) -> None:
        """Push a payload into the per-session ring buffer (skip internal events)."""
        evt = payload.get("event", "")
        if evt in ("ping", "pong", "message_chunk"):
            return
        q = self._recent_messages.setdefault(session_id, deque(maxlen=MESSAGE_QUEUE_SIZE))
        q.append(payload)
        # Degrade to summary if the backlog grows too large
        if len(q) > MAX_BACKLOG_MESSAGES:
            while len(q) > MESSAGE_QUEUE_SIZE // 2:
                q.popleft()

    async def replay_missed_messages(self, session_id: str, websocket: WebSocket, last_id: str | None) -> int:
        """Replay messages the client may have missed after reconnecting.

        Returns the count of replayed messages.  If *last_id* is provided,
        only messages after that ID are sent.
        """
        q = self._recent_messages.get(session_id)
        if not q:
            return 0

        replay: list[dict[str, Any]] = list(q)
        if last_id:
            found = False
            filtered: list[dict[str, Any]] = []
            for m in replay:
                if found:
                    filtered.append(m)
                elif m.get("id") == last_id or m.get("messageId") == last_id:
                    found = True
            replay = filtered

        for m in replay:
            if not await self._send_safe(websocket, {**m, "_replay": True}):
                return 0
        return len(replay)

    # ── Per-session serialisation lock ───────────────────────────────

    def get_session_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    # ── Stream-token lifecycle ───────────────────────────────────────

    def create_token(self, session_id: str) -> StreamToken:
        old = self._tokens.pop(session_id, None)
        if old and not old.cancelled:
            old.cancel()
        token = StreamToken()
        self._tokens[session_id] = token
        return token

    def cancel_token(self, session_id: str) -> None:
        token = self._tokens.get(session_id)
        if token and not token.cancelled:
            token.cancel()

    def remove_token(self, session_id: str, token: StreamToken) -> None:
        current = self._tokens.get(session_id)
        if current is token:
            self._tokens.pop(session_id, None)

    def has_active_stream(self, session_id: str) -> bool:
        token = self._tokens.get(session_id)
        return token is not None and not token.cancelled

    # ── Session cleanup ──────────────────────────────────────────────

    # ── PM/PMO event broadcasting ─────────────────────────────────────
    # Convenience helpers for PM → user interactions. Each broadcasts a
    # typed event that the frontend renders as an interactive bubble.

    async def broadcast_agent_question(
        self, session_id: str, message_id: str, agent_id: str,
        question: str, options: list[dict], allow_custom: bool = True,
    ) -> None:
        """Broadcast an agent_question event (PM asks user a clarifying question)."""
        await self.broadcast(session_id, {
            "event": "agent_question",
            "sessionId": session_id,
            "messageId": message_id,
            "agentId": agent_id,
            "question": question,
            "options": options,
            "allowCustomAnswer": allow_custom,
            "timestamp": db_now(),
        })

    async def broadcast_progress_update(
        self, session_id: str, message_id: str, agent_id: str,
        completed: int, total: int, current_step: str,
        eta_seconds: int | None = None,
    ) -> None:
        """Broadcast a progress_update event (PM reports task progress)."""
        payload: dict = {
            "event": "progress_update",
            "sessionId": session_id,
            "messageId": message_id,
            "agentId": agent_id,
            "completedSteps": completed,
            "totalSteps": total,
            "currentStep": current_step,
            "timestamp": db_now(),
        }
        if eta_seconds is not None:
            payload["estimatedRemainingSeconds"] = eta_seconds
        await self.broadcast(session_id, payload)

    async def broadcast_risk_warning(
        self, session_id: str, message_id: str, agent_id: str,
        risk_level: str, title: str, description: str,
        actions: list[dict],
    ) -> None:
        """Broadcast a risk_warning event (PM warns about a risk)."""
        await self.broadcast(session_id, {
            "event": "risk_warning",
            "sessionId": session_id,
            "messageId": message_id,
            "agentId": agent_id,
            "riskLevel": risk_level,
            "title": title,
            "description": description,
            "actions": actions,
            "timestamp": db_now(),
        })

    async def broadcast_agent_todo(
        self, session_id: str, message_id: str, agent_id: str,
        title: str, description: str, actions: list[dict],
        priority: str = "medium",
    ) -> None:
        """Broadcast an agent_todo event (PM pushes a decision to user)."""
        await self.broadcast(session_id, {
            "event": "agent_todo",
            "sessionId": session_id,
            "messageId": message_id,
            "agentId": agent_id,
            "title": title,
            "description": description,
            "actions": actions,
            "priority": priority,
            "timestamp": db_now(),
        })

    async def broadcast_pm_state(
        self, session_id: str, state: str, previous_state: str,
        details: str = "",
    ) -> None:
        """Broadcast a pm_state_change event (PM state machine transition)."""
        await self.broadcast(session_id, {
            "event": "pm_state_change",
            "sessionId": session_id,
            "state": state,
            "previousState": previous_state,
            "details": details,
            "timestamp": db_now(),
        })

    async def broadcast_task_preview(
        self, session_id: str, message_id: str,
        tasks: list[dict], eta_seconds: int | None = None,
    ) -> None:
        """Broadcast a task_preview event for user confirmation."""
        payload: dict = {
            "event": "task_preview",
            "sessionId": session_id,
            "messageId": message_id,
            "tasks": tasks,
            "timestamp": db_now(),
        }
        if eta_seconds is not None:
            payload["estimatedTotalSeconds"] = eta_seconds
        await self.broadcast(session_id, payload)

    async def broadcast_degradation_change(
        self, session_id: str, active: bool, reason: str,
        started_at: str, failed_models: list[str],
        recovery_attempts: int = 0,
    ) -> None:
        """Broadcast a degradation_change event."""
        await self.broadcast(session_id, {
            "event": "degradation_change",
            "sessionId": session_id,
            "status": {
                "active": active,
                "reason": reason,
                "startedAt": started_at,
                "failedModels": failed_models,
                "recoveryAttempts": recovery_attempts,
            },
            "timestamp": db_now(),
        })

    # ── Session cleanup ──────────────────────────────────────────────

    def teardown_session(self, session_id: str) -> None:
        self.cancel_token(session_id)
        self._tokens.pop(session_id, None)
        self._locks.pop(session_id, None)
        conns = self._connections.pop(session_id, None)
        if conns:
            for cid, _, _, _ in conns:
                self._heartbeats.pop((session_id, cid), None)
        # Keep recent messages for a while in case of reconnection
        # (they'll age out via deque maxlen)


manager = WebSocketManager()
