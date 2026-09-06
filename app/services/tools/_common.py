"""Shared imports, constants and helpers for builtin tools.

Split out of ``builtin_tools.py`` — see ``web_search.py``, ``file_ops.py``,
``code_execute.py``, ``memory.py`` for the actual tool implementations.
"""

from __future__ import annotations

import logging
import os
import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.config import MEMORY_DIR
from app.services.tools.sandbox_executor import sandbox_executor
from app.utils.async_file import (
    aexists,
    aisfile,
    aisdir,
    aread_text,
    awrite_text,
    astat_size,
    aiterdir,
    amkdir,
)

logger = logging.getLogger("agenthub.tools.builtin")

# ── Security constraints ──────────────────────────────────────────────
MAX_FILE_READ_BYTES = 1_000_000  # 1 MB
MAX_FILE_LINES = 2000
CODE_EXECUTE_TIMEOUT = 30  # seconds (script execution)
CODE_EXECUTE_INSTALL_TIMEOUT = 120  # seconds (pip/npm install)
MAX_CODE_OUTPUT_CHARS = 10_000


def file_sha256(path: Path) -> str | None:
    """Return the full SHA-256 of one file, or ``None`` when absent/unreadable."""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def validate_expected_sha256(path: Path, expected_sha256: str | None) -> tuple[bool, str]:
    """Enforce optimistic concurrency for a file mutation.

    An empty expected hash is valid only when the target does not exist yet.
    Existing files require the full hash returned by ``file_read``.
    """
    if expected_sha256 is None:
        return False, "expected_sha256 是必填参数；请先调用 file_read 获取完整 sha256"
    expected = str(expected_sha256).strip().lower().removeprefix("sha256:")
    current = file_sha256(path)
    if current is None:
        if expected in {"", "missing", "none"}:
            return True, ""
        return False, f"文件不存在，但 expected_sha256={expected} 表示文件应已存在"
    if not expected:
        return False, "目标文件已存在，expected_sha256 不能为空"
    if expected != current:
        return False, (
            "文件已被外部修改，拒绝写入（expected_sha256 与当前文件不一致）；"
            f"expected={expected[:12]} current={current[:12]}"
        )
    return True, ""


def _safe_path(file_path: str, base: Path) -> Path | None:
    """Resolve a path and ensure it stays within the allowed base directory."""
    try:
        resolved = (base / file_path).resolve()
        if not str(resolved).startswith(str(base.resolve())):
            return None
        return resolved
    except (OSError, ValueError):
        return None


# ── Diff helper (used by file_write / file_patch for broadcast) ────────────

def _compute_unified_diff(old_text: str, new_text: str, path: str = "") -> str:
    """Compute a unified diff between two text strings.  Returns empty string
    when the texts are identical or difflib is unavailable."""
    if old_text == new_text:
        return ""
    try:
        import difflib
        diff_lines = list(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=path or "a/file",
                tofile=path or "b/file",
                lineterm="",
            )
        )
        return "".join(diff_lines[:100])  # cap at 100 lines for broadcast
    # noqa: BLE001 - best-effort, never block main path
    except Exception:
        return ""


# ── Fire-and-forget helpers (must NEVER block the tool response) ───────────

async def _broadcast_workspace_change(
    session_id: str, path: str, operation: str, size_bytes: int,
    diff_preview: str = "", old_path: str = "", user_id: str = "",
) -> None:
    """Broadcast a workspace_change event after a successful file op."""
    try:
        from app.services.websocket_manager import manager
        if session_id:
            await manager.broadcast_workspace_change(
                session_id=session_id, path=path, operation=operation,
                user_id=user_id, size_bytes=size_bytes,
                diff_preview=diff_preview, old_path=old_path,
            )
    # noqa: BLE001 - best-effort (注释已说明故意吞)
    except Exception:
        pass  # broadcast failure must never block the tool


async def _record_file_version(
    path: str, content: str, session_id: str = "", user_id: str = "",
) -> str:
    """Record a file version and return the SHA-256 hash."""
    try:
        from app.services.file_version_tracker import file_version_tracker
        sid = session_id or _get_sid_fast()
        uid = user_id or _get_uid_fast()
        fv = file_version_tracker.record_write(sid, path, content, uid)
        return fv.sha256
    # noqa: BLE001 - best-effort, never block main path
    except Exception:
        return ""


async def _auto_git_commit(path: str, user_id: str, operation: str) -> None:
    """Auto-commit to git after a file write (fire-and-forget)."""
    try:
        from app.config import AGENTHUB_FILE_AUTO_GIT
        if not AGENTHUB_FILE_AUTO_GIT:
            return
        import asyncio as _asyncio
        from app.services.git_service import git_service
        await _asyncio.to_thread(git_service.auto_commit, path, user_id, operation)
    # noqa: BLE001 - git status is non-critical, degrade gracefully
    except Exception:
        pass


def _get_sid_fast() -> str:
    try:
        from app.services.workspace_context import get_workspace_session_id
        return get_workspace_session_id()
    # noqa: BLE001 - best-effort, never block main path
    except Exception:
        return ""


def _get_uid_fast() -> str:
    try:
        from app.services.workspace_context import get_workspace_user_id
        return get_workspace_user_id()
    # noqa: BLE001 - best-effort, never block main path
    except Exception:
        return ""
