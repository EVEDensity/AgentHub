"""Session-level RBAC guard for multi-user collaboration.

Provides FastAPI dependency injection that validates a user has the
required role (owner / member / viewer) for a given session before
allowing the request to proceed.

Usage::

    from app.services.auth.session_guard import require_session_role, SessionRole

    @router.delete("/sessions/{session_id}")
    async def delete_session(
        session_id: str,
        access: SessionAccess = Depends(require_session_role(minimum_role=SessionRole.OWNER)),
    ) -> dict:
        # Only owners reach this point
        ...

    # For WebSocket — manual check (can't use Depends in ws endpoint)
    access = await check_session_access(session_id, user)
    if not access.can_write:
        await ws.close(code=4003)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import logging
import uuid
from typing import Any

from fastapi import Depends, HTTPException

from app.db.init_db import now
from app.db.session import aexecute, afetch_one
from app.services.auth.service import get_current_user

logger = logging.getLogger("agenthub.session_guard")


# ── Audit trail helpers ──────────────────────────────────────────────────


async def audit_session_event(
    session_id: str,
    user_id: str,
    action: str,
    target_user: str = "",
    details: str = "",
) -> None:
    """Log a session collaboration event to the audit_log table.

    Args:
        session_id: The session where the event occurred.
        user_id: The user who performed the action.
        action: One of 'access_granted', 'access_denied', 'member_invited',
                'member_removed', 'role_changed', 'session_joined',
                'visibility_changed', 'ownership_transferred'.
        target_user: The affected user (if applicable).
        details: Human-readable description.
    """
    try:
        payload = f"session={session_id} target={target_user} {details}".strip()
        await aexecute(
            "INSERT INTO audit_log (id, user_id, agent_id, action, risk_level, decision, "
            "content_hash, payload_json, timestamp) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            str(uuid.uuid4()),
            user_id,
            "",  # agent_id — not applicable for session management
            f"session:{action}",
            "low",
            "allow",
            hashlib.sha256(payload.encode()).hexdigest(),
            payload,
            now(),
        )
    except Exception:
        logger.debug("audit_session_event failed action=%s session=%s", action, session_id, exc_info=True)


class SessionRole(str, Enum):
    OWNER = "owner"
    MEMBER = "member"
    VIEWER = "viewer"


# ── Role rank for hierarchical comparison ────────────────────────────────

_ROLE_RANK: dict[SessionRole, int] = {
    SessionRole.VIEWER: 1,
    SessionRole.MEMBER: 2,
    SessionRole.OWNER: 3,
}


@dataclass(frozen=True)
class SessionAccess:
    """Lightweight capability object returned after access validation.

    All boolean fields are derived from *role* and cached at init time
    so downstream code can branch on ``access.can_write`` without
    repeated enum comparisons.
    """

    user_id: str
    session_id: str
    role: SessionRole
    can_write: bool = field(init=False)
    can_manage: bool = field(init=False)
    can_invite: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "can_write", self.role in (SessionRole.OWNER, SessionRole.MEMBER))
        object.__setattr__(self, "can_manage", self.role == SessionRole.OWNER)
        object.__setattr__(self, "can_invite", self.role == SessionRole.OWNER)


# ── Public API ───────────────────────────────────────────────────────────


async def check_session_access(
    session_id: str,
    user: dict[str, Any],
) -> SessionAccess:
    """Validate that *user* has access to *session_id*.

    Resolution order:
    1. Direct membership → use stored role
    2. Session is public → default to viewer
    3. Otherwise → HTTP 403

    Returns a ``SessionAccess`` capability object.
    Never returns ``None`` — raises ``HTTPException`` on denied access.
    """
    # 1. Check direct membership
    row = await afetch_one(
        "SELECT role FROM session_members WHERE session_id=$1 AND user_id=$2",
        session_id, user["id"],
    )
    if row:
        return SessionAccess(
            user_id=user["id"],
            session_id=session_id,
            role=SessionRole(row["role"]),
        )

    # 2. Check public access
    sess = await afetch_one(
        "SELECT visibility FROM sessions WHERE id=$1", session_id
    )
    if sess and sess.get("visibility") == "public":
        return SessionAccess(
            user_id=user["id"],
            session_id=session_id,
            role=SessionRole.VIEWER,
        )

    # 3. Deny — audit trail for security monitoring
    await audit_session_event(
        session_id, user["id"], "access_denied",
        details=f"User '{user.get('name', user['id'])}' attempted access without membership",
    )
    raise HTTPException(
        status_code=403,
        detail=f"Access denied to session '{session_id}'",
    )


# ── FastAPI dependency ────────────────────────────────────────────────────


def require_session_role(
    minimum_role: SessionRole = SessionRole.VIEWER,
) -> Any:
    """Factory that returns a FastAPI dependency validating session role.

    Args:
        minimum_role: The lowest role allowed.  Hierarchy is
            viewer < member < owner.

    Usage::

        @router.delete("/sessions/{sid}")
        async def delete_session(
            sid: str,
            access: SessionAccess = Depends(require_session_role(SessionRole.OWNER)),
        ):
            ...
    """

    async def _guard(
        session_id: str,
        user: dict[str, Any] = Depends(get_current_user),
    ) -> SessionAccess:
        access = await check_session_access(session_id, user)

        if _ROLE_RANK[access.role] < _ROLE_RANK[minimum_role]:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{access.role.value}' insufficient — "
                f"requires '{minimum_role.value}' or higher",
            )

        return access

    return _guard
