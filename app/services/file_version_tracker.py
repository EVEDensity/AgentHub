"""Per-session file version tracking for multi-user conflict detection.

Tracks SHA-256 hashes of workspace files to detect concurrent
modifications by different users or agents.  In-memory storage —
lost on restart, but the filesystem is the source of truth; this
is an advisory layer.

Usage::

    from app.services.file_version_tracker import file_version_tracker

    # After reading a file, record the version for later conflict checks:
    fv = file_version_tracker.record_read(session_id, path, content)

    # Before writing, check if someone else modified the file:
    conflict = file_version_tracker.check_conflict(session_id, path, fv.sha256)
    if conflict["conflict"]:
        print("File was modified by", conflict["current_version"].written_by_user)

    # After writing, record the new version:
    file_version_tracker.record_write(session_id, path, content, user_id, agent_id)
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

# ── Data model ─────────────────────────────────────────────────────────────


@dataclass
class FileVersion:
    """Snapshot of a workspace file at a point in time."""

    sha256: str
    written_by_user: str = ""
    written_by_agent: str = ""
    written_at: float = 0.0  # time.monotonic()
    size_bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256[:12],
            "written_by_user": self.written_by_user,
            "written_by_agent": self.written_by_agent,
            "written_at": self.written_at,
            "size_bytes": self.size_bytes,
        }


# ── Tracker ─────────────────────────────────────────────────────────────────


class FileVersionTracker:
    """Tracks file hashes per session for concurrent-edit detection.

    Each :meth:`record_write` stores a ``FileVersion`` keyed by
    ``(session_id, rel_path)``.  :meth:`check_conflict` compares the
    stored hash against an *expected* hash (from a previous read) to
    determine whether another user or agent modified the file in the
    meantime.
    """

    def __init__(self) -> None:
        # session_id → {rel_path: FileVersion}
        self._store: dict[str, dict[str, FileVersion]] = {}

    # ── Hashing ────────────────────────────────────────────────────────

    @staticmethod
    def hash_content(content: str | bytes) -> str:
        """Return the SHA-256 hex digest of *content*."""
        if isinstance(content, str):
            content = content.encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    # ── Record operations ──────────────────────────────────────────────

    def record_read(
        self,
        session_id: str,
        path: str,
        content: str,
        user_id: str = "",
        agent_id: str = "",
    ) -> FileVersion:
        """Record a file read so the hash can be used for later conflict checks.

        This is called by ``file_read_handler`` so the frontend / agent
        has a baseline hash to pass back when writing.
        """
        fv = FileVersion(
            sha256=self.hash_content(content),
            written_by_user=user_id,
            written_by_agent=agent_id,
            written_at=time.monotonic(),
            size_bytes=len(content.encode("utf-8")),
        )
        self._store.setdefault(session_id, {})[path] = fv
        return fv

    def record_write(
        self,
        session_id: str,
        path: str,
        content: str,
        user_id: str = "",
        agent_id: str = "",
    ) -> FileVersion:
        """Record a file write, updating the tracked version."""
        fv = FileVersion(
            sha256=self.hash_content(content),
            written_by_user=user_id,
            written_by_agent=agent_id,
            written_at=time.monotonic(),
            size_bytes=len(content.encode("utf-8")),
        )
        self._store.setdefault(session_id, {})[path] = fv
        return fv

    def record_delete(self, session_id: str, path: str) -> None:
        """Remove version tracking for a deleted file."""
        store = self._store.get(session_id)
        if store:
            store.pop(path, None)

    # ── Conflict detection ─────────────────────────────────────────────

    def check_conflict(
        self,
        session_id: str,
        path: str,
        expected_hash: str | None,
    ) -> dict[str, Any]:
        """Check whether *path* was modified since *expected_hash* was read.

        Args:
            session_id: Current session.
            path: Relative file path within the workspace.
            expected_hash: The SHA-256 the caller expects (from ``record_read``
                or a previous ``record_write``).

        Returns:
            ``{"conflict": False, "current_version": FileVersion | None, "message": str}``
            when the file hasn't changed or we have no record.

            ``{"conflict": True, ...}`` when the tracked hash differs from
            *expected_hash*, meaning someone else wrote to the file.
        """
        current = self._store.get(session_id, {}).get(path)
        if current is None:
            return {"conflict": False, "current_version": None, "message": "no history for this file"}
        if expected_hash and current.sha256 != expected_hash:
            return {
                "conflict": True,
                "current_version": current,
                "message": (
                    f"文件在读取后被 {current.written_by_user or current.written_by_agent or '其他用户'} "
                    f"修改 ({current.written_at})"
                ),
            }
        return {"conflict": False, "current_version": current, "message": "ok"}

    def get_last_writer(self, session_id: str, path: str) -> FileVersion | None:
        """Return the last recorded version for *path*, or None."""
        return self._store.get(session_id, {}).get(path)

    # ── Maintenance ────────────────────────────────────────────────────

    def clear_session(self, session_id: str) -> None:
        """Remove all tracking data for a session (called on teardown)."""
        self._store.pop(session_id, None)

    @property
    def tracked_files(self) -> dict[str, int]:
        """Return {session_id: count} for monitoring."""
        return {sid: len(files) for sid, files in self._store.items()}


# ── Singleton ──────────────────────────────────────────────────────────────

file_version_tracker = FileVersionTracker()
