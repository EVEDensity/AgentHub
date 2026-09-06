"""Git tool handlers — read-only + one safe write (commit).

Split out of the ad-hoc tool pool to give agents a consistent git
interface across runners.  All commands run inside the workspace root;
no ``cd``, no arbitrary git flags, no force-push or branch deletion.

Handlers return ``dict`` with ``success`` + ``result`` fields so the
FunctionTool registry can wrap them uniformly.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("agenthub.tools.git")

MAX_GIT_OUTPUT_CHARS = 8_000


async def _run_git(args: list[str], *, cwd: Path) -> dict[str, Any]:
    """Run ``git <args>`` inside *cwd* and capture stdout/stderr."""
    if not cwd.exists():
        return {"success": False, "error": f"workspace not found: {cwd}"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        return {"success": False, "error": "git executable not found on PATH"}
    except Exception as exc:  # noqa: BLE001 - subprocess errors become safe results
        return {"success": False, "error": f"git execution failed: {exc}"}

    out = stdout.decode("utf-8", errors="replace") if stdout else ""
    err = stderr.decode("utf-8", errors="replace") if stderr else ""
    if len(out) > MAX_GIT_OUTPUT_CHARS:
        out = out[:MAX_GIT_OUTPUT_CHARS] + "\n…[truncated]"
    if len(err) > MAX_GIT_OUTPUT_CHARS:
        err = err[:MAX_GIT_OUTPUT_CHARS] + "\n…[truncated]"

    if proc.returncode != 0:
        return {
            "success": False,
            "result": f"[exit {proc.returncode}]\n{err or out}",
            "exit_code": proc.returncode,
        }
    return {
        "success": True,
        "result": out or "(no output)",
        "stderr": err,
        "exit_code": 0,
    }


async def git_status_handler(
    cwd: str = ".",
    *,
    porcelain: bool = True,
    branch: bool = True,
) -> dict[str, Any]:
    """Run ``git status`` with optional branch summary."""
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(cwd) if cwd != "." else ws_root
    if safe is None:
        return {"success": False, "error": f"cwd '{cwd}' outside workspace"}

    args = ["status", "--porcelain" if porcelain else "--short"]
    if branch:
        args.insert(1, "-b")
    return await _run_git(args, cwd=safe)


async def git_diff_handler(
    cwd: str = ".",
    *,
    staged: bool = False,
    path: str | None = None,
    context: int = 3,
) -> dict[str, Any]:
    """Run ``git diff`` — unstaged changes by default, or --cached for staged."""
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(cwd) if cwd != "." else ws_root
    if safe is None:
        return {"success": False, "error": f"cwd '{cwd}' outside workspace"}

    args = ["diff"]
    if staged:
        args.append("--cached")
    args.extend(["--unified", str(max(0, min(context, 10)))])
    if path:
        args.append(path)
    return await _run_git(args, cwd=safe)


async def git_log_handler(
    cwd: str = ".",
    *,
    count: int = 10,
    oneline: bool = True,
) -> dict[str, Any]:
    """Run ``git log`` with a short default format."""
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(cwd) if cwd != "." else ws_root
    if safe is None:
        return {"success": False, "error": f"cwd '{cwd}' outside workspace"}

    count = max(1, min(count, 50))
    args = ["log", f"-n{count}"]
    if oneline:
        args.append("--oneline")
    else:
        args.extend(["--format=%h %an %ad %s", "--date=short"])
    return await _run_git(args, cwd=safe)


async def git_commit_handler(
    message: str,
    *,
    cwd: str = ".",
    all_files: bool = True,
) -> dict[str, Any]:
    """Run ``git commit`` — a *safe* write op that only stages + commits."""
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    if not message or not message.strip():
        return {"success": False, "error": "commit message is required"}

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(cwd) if cwd != "." else ws_root
    if safe is None:
        return {"success": False, "error": f"cwd '{cwd}' outside workspace"}

    # Stage all tracked changes first (--all covers modified + deleted)
    stage_args = ["add", "-A"]
    stage_result = await _run_git(stage_args, cwd=safe)
    if not stage_result.get("success"):
        return stage_result

    commit_args = ["commit", "-m", message.strip()]
    if all_files:
        commit_args.insert(1, "--all")
    return await _run_git(commit_args, cwd=safe)


async def git_branch_handler(
    cwd: str = ".",
    *,
    show_current: bool = True,
) -> dict[str, Any]:
    """Run ``git branch`` — list branches with current marked."""
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(cwd) if cwd != "." else ws_root
    if safe is None:
        return {"success": False, "error": f"cwd '{cwd}' outside workspace"}

    args = ["branch"]
    return await _run_git(args, cwd=safe)


async def git_branch_create_handler(name: str, cwd: str = ".") -> dict[str, Any]:
    """Create and switch to a new branch; never overwrite an existing branch."""
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path
    if not name.strip() or name.startswith("-") or any(ch in name for ch in " ~^:?*[\\"):
        return {"success": False, "error": "无效或不安全的分支名"}
    ws_root = get_workspace_root()
    safe = resolve_workspace_path(cwd) if cwd != "." else ws_root
    if safe is None:
        return {"success": False, "error": f"cwd '{cwd}' outside workspace"}
    return await _run_git(["switch", "-c", name.strip()], cwd=safe)


async def git_revert_handler(commit: str, cwd: str = ".") -> dict[str, Any]:
    """Create a revert commit for one exact commit; no reset/force operations."""
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path
    if not commit.strip() or commit.startswith("-") or any(ch.isspace() for ch in commit):
        return {"success": False, "error": "commit 必须是单个 commit id"}
    ws_root = get_workspace_root()
    safe = resolve_workspace_path(cwd) if cwd != "." else ws_root
    if safe is None:
        return {"success": False, "error": f"cwd '{cwd}' outside workspace"}
    return await _run_git(["revert", "--no-edit", commit.strip()], cwd=safe)


async def git_cherry_pick_handler(commit: str, cwd: str = ".") -> dict[str, Any]:
    """Apply one exact commit, preserving Git's normal conflict state."""
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path
    if not commit.strip() or commit.startswith("-") or any(ch.isspace() for ch in commit):
        return {"success": False, "error": "commit 必须是单个 commit id"}
    ws_root = get_workspace_root()
    safe = resolve_workspace_path(cwd) if cwd != "." else ws_root
    if safe is None:
        return {"success": False, "error": f"cwd '{cwd}' outside workspace"}
    return await _run_git(["cherry-pick", commit.strip()], cwd=safe)


HANDLERS = {
    "git_status": git_status_handler,
    "git_diff": git_diff_handler,
    "git_log": git_log_handler,
    "git_commit": git_commit_handler,
    "git_branch": git_branch_handler,
    "git_branch_create": git_branch_create_handler,
    "git_revert": git_revert_handler,
    "git_cherry_pick": git_cherry_pick_handler,
}


def get_handler(name: str):
    return HANDLERS.get(name)


__all__ = [
    "HANDLERS",
    "get_handler",
    "git_branch_handler",
    "git_commit_handler",
    "git_diff_handler",
    "git_log_handler",
    "git_status_handler",
    "git_branch_create_handler",
    "git_revert_handler",
    "git_cherry_pick_handler",
]
