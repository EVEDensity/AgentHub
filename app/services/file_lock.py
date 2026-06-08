"""Advisory file locks for multi-user workspace coordination.

Locks are in-memory (per-process) and auto-expire after LOCK_TTL seconds.
They are **advisory** — they don't block writes; they warn.  This is
intentional: in a collaborative coding environment users should always be
able to override a stale lock.

Usage::

    from app.services.file_lock import file_lock_manager

    # Acquire before editing
    result = file_lock_manager.acquire(session_id, path, user_id, agent_id)
    if result["ok"]:
        # ... edit the file ...
        file_lock_manager.release(session_id, path, user_id)

    # Check if someone else holds a lock
    existing = file_lock_manager.check(session_id, path)
    if existing:
        print(f"Locked by {existing.holder_user_id}")
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

LOCK_TTL = 60  # seconds — auto-expire after this duration

# ── Data model ─────────────────────────────────────────────────────────────


@dataclass
class FileLock:
    """Represents an advisory lock on a workspace file."""

    path: str
    session_id: str
    holder_user_id: str
    holder_agent_id: str
    holder_name: str = ""
    acquired_at: float = 0.0
    expires_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "holder_user_id": self.holder_user_id,
            "holder_agent_id": self.holder_agent_id,
            "holder_name": self.holder_name,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
            "remaining_seconds": self.remaining_seconds,
        }


# ── Lock manager ───────────────────────────────────────────────────────────


class FileLockManager:
    """Advisory file-lock registry.

    Locks are keyed by ``(session_id, path)``.  A lock held by the same
    user is automatically renewed on re-acquire (idempotent).  Expired
    locks are silently cleaned up on every :meth:`acquire` call.
    """

    def __init__(self) -> None:
        # (session_id, path) → FileLock
        self._locks: dict[tuple[str, str], FileLock] = {}
        # Statistics
        self._acquire_count: dict[str, int] = defaultdict(int)

    # ── Core operations ────────────────────────────────────────────────

    def acquire(
        self,
        session_id: str,
        path: str,
        user_id: str,
        agent_id: str = "",
        holder_name: str = "",
    ) -> dict[str, Any]:
        """Attempt to acquire an advisory lock on *path*.

        Returns:
            ``{"ok": True, "lock": FileLock, "message": str}`` on success
            or renewal.

            ``{"ok": False, "lock": FileLock, "message": str}`` when the
            lock is held by a *different* user.
        """
        self._expire_stale()  # clean up expired locks first
        key = (session_id, path)
        now = time.monotonic()

        existing = self._locks.get(key)
        if existing is not None:
            if existing.holder_user_id == user_id:
                # Same user — renew
                existing.expires_at = now + LOCK_TTL
                existing.holder_name = holder_name or existing.holder_name
                return {
                    "ok": True,
                    "lock": existing,
                    "message": "已续期",
                    "conflict": False,
                }
            # Different user — conflict
            return {
                "ok": False,
                "lock": existing,
                "message": (
                    f"文件被 {existing.holder_name or existing.holder_user_id} "
                    f"锁定，{existing.remaining_seconds:.0f}秒后过期"
                ),
                "conflict": True,
            }

        # Fresh lock
        lock = FileLock(
            path=path,
            session_id=session_id,
            holder_user_id=user_id,
            holder_agent_id=agent_id,
            holder_name=holder_name,
            acquired_at=now,
            expires_at=now + LOCK_TTL,
        )
        self._locks[key] = lock
        self._acquire_count[user_id] += 1
        return {"ok": True, "lock": lock, "message": "已锁定", "conflict": False}

    def release(
        self, session_id: str, path: str, user_id: str = ""
    ) -> bool:
        """Release the lock on *path*.

        If *user_id* is provided, only releases if the caller holds the lock.
        Returns ``True`` if a lock was released.
        """
        key = (session_id, path)
        lock = self._locks.get(key)
        if lock is None:
            return False
        if user_id and lock.holder_user_id != user_id:
            return False
        del self._locks[key]
        return True

    def check(self, session_id: str, path: str) -> FileLock | None:
        """Return the current lock on *path*, or ``None`` if free/expired."""
        self._expire_stale()
        lock = self._locks.get((session_id, path))
        if lock and not lock.is_expired:
            return lock
        return None

    def renew(
        self, session_id: str, path: str, user_id: str
    ) -> bool:
        """Extend the lock TTL.  Returns ``True`` on success."""
        lock = self._locks.get((session_id, path))
        if lock and lock.holder_user_id == user_id:
            lock.expires_at = time.monotonic() + LOCK_TTL
            return True
        return False

    # ── Bulk operations ────────────────────────────────────────────────

    def release_all_for_user(self, session_id: str, user_id: str) -> int:
        """Release all locks held by *user_id* in a session.  Returns count."""
        removed = 0
        for key, lock in list(self._locks.items()):
            if lock.session_id == session_id and lock.holder_user_id == user_id:
                del self._locks[key]
                removed += 1
        return removed

    def list_locks(self, session_id: str) -> list[dict[str, Any]]:
        """Return all active locks for a session."""
        self._expire_stale()
        return [
            lock.as_dict()
            for (sid, _), lock in self._locks.items()
            if sid == session_id
        ]

    # ── Maintenance ────────────────────────────────────────────────────

    def _expire_stale(self) -> int:
        """Remove expired locks.  Returns the count removed."""
        now = time.monotonic()
        stale = [
            key for key, lock in self._locks.items() if now > lock.expires_at
        ]
        for key in stale:
            del self._locks[key]
        return len(stale)

    @property
    def active_lock_count(self) -> int:
        self._expire_stale()
        return len(self._locks)


# ── Singleton ──────────────────────────────────────────────────────────────

file_lock_manager = FileLockManager()
