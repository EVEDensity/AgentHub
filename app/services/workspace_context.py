"""Per-user, per-session workspace context via contextvars.

All agent tools (file_read, file_write, code_execute, git, etc.) resolve
their working directory through this module so every user + session pair
gets an independent, isolated workspace on disk.

Directory layout:  DATA_DIR/workspaces/{user_id}/{session_id}/

Usage::

    from app.services.workspace_context import (
        get_workspace_root,
        set_workspace_context,
        resolve_workspace_path,
    )

    # Set once per request/session — propagates to all nested calls:
    set_workspace_context(user_id="abc", session_id="sess-1")

    # Anywhere downstream (tools, services, hooks):
    root = get_workspace_root()
    safe = resolve_workspace_path("src/main.py")  # validates within root
"""

from __future__ import annotations

import contextvars
import re
import unicodedata
from pathlib import Path

from app.config import WORKSPACES_DIR

# ── Public helpers (also used by files.py) ────────────────────────────────

def slugify_user_dir(user_id: str) -> str:
    """Convert user ID to a safe directory name."""
    value = unicodedata.normalize("NFKD", str(user_id)).strip()
    value = re.sub(r"[^\w.-]", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    value = re.sub(r"\.{2,}", ".", value)
    value = value.strip("-.").lower()
    return value or "unknown-user"


def slugify_session_dir(session_id: str) -> str:
    """Convert session ID to a safe directory name."""
    value = unicodedata.normalize("NFKD", str(session_id)).strip()
    value = re.sub(r"[^\w.-]", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    value = value.strip("-.").lower()
    return value or "default-session"


def build_workspace_root(user_id: str, session_id: str) -> Path:
    """Construct (but do not create) the workspace root for a user+session pair."""
    user_dir = slugify_user_dir(user_id)
    sess_dir = slugify_session_dir(session_id)
    root = (WORKSPACES_DIR / user_dir / sess_dir).resolve()
    _ensure_within_workspaces(root)
    return root


def build_user_workspace_root(user_id: str) -> Path:
    """Construct the top-level per-user directory (contains all sessions)."""
    user_dir = slugify_user_dir(user_id)
    root = (WORKSPACES_DIR / user_dir).resolve()
    _ensure_within_workspaces(root)
    return root


def _ensure_within_workspaces(path: Path) -> None:
    """Safety valve — should never fire with slug output."""
    try:
        path.relative_to(WORKSPACES_DIR.resolve())
    except ValueError:
        raise ValueError(f"Workspace path {path} escapes WORKSPACES_DIR")


# ── ContextVar plumbing ───────────────────────────────────────────────────

_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "workspace_user_id", default=""
)
_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "workspace_session_id", default=""
)


def set_workspace_context(user_id: str, session_id: str) -> Path:
    """Set the current workspace context and return the resolved root.

    Must be called at the entry point of every request / WebSocket session.
    The returned root is guaranteed to exist on disk.
    """
    _current_user_id.set(user_id)
    _current_session_id.set(session_id)
    root = build_workspace_root(user_id, session_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_workspace_user_id() -> str:
    """Return the current user id (or empty string if not set)."""
    return _current_user_id.get()


def get_workspace_session_id() -> str:
    """Return the current session id (or empty string if not set)."""
    return _current_session_id.get()


def get_workspace_root() -> Path:
    """Return the current user+session workspace root.

    If no context has been set, falls back to a default directory
    under ``WORKSPACES_DIR`` so tools never operate on the project root.
    """
    uid = _current_user_id.get()
    sid = _current_session_id.get()
    if not uid or not sid:
        # Fallback — tools invoked without explicit context get a sandbox
        uid = uid or "unknown"
        sid = sid or "orphan"
    root = build_workspace_root(uid, sid)
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_workspace_path(rel_path: str) -> Path | None:
    """Resolve a relative path inside the current workspace root.

    Returns ``None`` when the path would escape the workspace
    (path-traversal protection).
    """
    root = get_workspace_root()
    try:
        resolved = (root / rel_path).resolve()
        resolved.relative_to(root)
        return resolved
    except (ValueError, OSError):
        return None
