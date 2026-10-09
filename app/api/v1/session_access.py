"""Session scope checks shared by the v1 chat and event adapters."""
from __future__ import annotations

from fastapi import HTTPException

from app.domain import Session
from app.repositories import SessionRepository


async def require_session_workspace(
    session_id: str,
    workspace_id: str,
    sessions: SessionRepository,
) -> Session:
    """Resolve durable ownership after the caller authorizes the workspace.

    A client-supplied workspace or an event's session ID cannot establish
    ownership. Missing, legacy-unscoped, and other-workspace sessions have
    the same response, so callers cannot probe another workspace's sessions.
    """
    session = await sessions.get_session(session_id)
    if session is None or session.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return session
