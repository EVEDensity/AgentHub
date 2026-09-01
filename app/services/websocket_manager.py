"""Null-op WebSocket manager stub.

The production WebSocket transport was removed in favour of Mission/SSE.
This stub keeps downstream imports (admin MCP, agent tooling, persistence,
builtin_tools) working — every broadcast method is a no-op, connection
counts always report zero, and token/session lookups return empty results.

Safe to delete once all remaining callers are migrated off the broadcast API.
"""

from __future__ import annotations

from typing import Any


class _NullOpToken:
    """Stand-in for a real asyncio Task token."""

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class WebSocketManager:
    """No-op connection manager — zero connections, zero broadcasts."""

    def __init__(self) -> None:
        self._session_tokens: dict[str, list[_NullOpToken]] = {}

    # ── Introspection ───────────────────────────────────────────────

    def active_connection_count(self) -> int:
        return 0

    def get_connections_for_session(self, session_id: str) -> list:
        return []

    def get_tokens_for_session(self, session_id: str) -> list[_NullOpToken]:
        return list(self._session_tokens.get(session_id, []))

    # ── Broadcasts (all no-op) ──────────────────────────────────────

    async def broadcast(self, session_id: str, payload: dict[str, Any]) -> None: ...
    async def broadcast_user_event(self, session_id: str, event: Any) -> None: ...
    async def broadcast_agent_question(self, session_id: str, question: Any) -> None: ...
    async def broadcast_progress_update(self, session_id: str, progress: Any) -> None: ...
    async def broadcast_risk_warning(self, session_id: str, warning: Any) -> None: ...
    async def broadcast_agent_todo(self, session_id: str, todo: Any) -> None: ...
    async def broadcast_pm_state(self, session_id: str, *args: Any, **kw: Any) -> None: ...
    async def broadcast_task_preview(self, session_id: str, preview: Any) -> None: ...
    async def broadcast_solution_proposal(self, session_id: str, proposal: Any) -> None: ...
    async def broadcast_interaction_already_resolved(self, session_id: str) -> None: ...
    async def broadcast_permission_mode_changed(self, session_id: str, *args: Any, **kw: Any) -> None: ...
    async def broadcast_degradation_change(self, session_id: str, *args: Any, **kw: Any) -> None: ...
    async def broadcast_deploy_card(self, session_id: str, card: Any) -> None: ...
    async def broadcast_workspace_change(self, session_id: str, *args: Any, **kw: Any) -> None: ...
    async def broadcast_file_conflict(self, session_id: str, *args: Any, **kw: Any) -> None: ...
    async def broadcast_file_lock_change(self, session_id: str, *args: Any, **kw: Any) -> None: ...


# Module-level singleton — retained for ``from app.services.websocket_manager import manager`` compatibility
manager = WebSocketManager()
