"""Workspace status and diff API endpoints.

Mimics the workspaceService.ts + sessionRewindService.ts pattern from the
cc-haha reference architecture, adapted to FastAPI + Python:

    GET  /api/workspace/status        — changed files list with +/- counts
    GET  /api/workspace/diff          — unified diff for a single file
    GET  /api/workspace/file          — read file content (mirrors files/workspace/read)
"""

from __future__ import annotations

import difflib
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import WORKSPACES_DIR
from app.services.auth_service import get_current_user
from app.services.workspace_context import get_workspace_root
from app.utils.async_file import aexists, aread_text, astat_size

logger = logging.getLogger("agenthub.api.workspace")

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

# ── Constants ──────────────────────────────────────────────────────────

MAX_DIFF_LINES = 5000
MAX_DIFF_CHARS = 250_000

# ── Response models (plain dicts for simplicity) ──────────────────────


@router.get("/status")
async def get_workspace_status(
    session_id: str = Query("", alias="sessionId"),
    user: dict = Depends(get_current_user),
):
    """Return workspace status: git branch, changed files with +/- counts.

    Priority:
    1. Git status (if the workspace is a git repo) — most accurate
    2. Session file changes (from DB messages) — fallback
    3. File system scan — last resort
    """
    user_id = user["id"]
    work_dir = get_workspace_root()

    result: dict = {
        "state": "ok",
        "workDir": str(work_dir),
        "repoName": None,
        "branch": None,
        "isGitRepo": False,
        "changedFiles": [],
    }

    # ── 1. Try git status ──────────────────────────────────────────
    try:
        from app.services.git_service import GitService

        git = GitService(work_dir)
        is_git = (work_dir / ".git").exists()

        if is_git:
            result["isGitRepo"] = True
            try:
                result["branch"] = git._run(["rev-parse", "--abbrev-ref", "HEAD"])
            except Exception:
                result["branch"] = "main"

            try:
                repo_name = git._run(["rev-parse", "--show-toplevel"])
                result["repoName"] = Path(repo_name).name
            except Exception:
                pass

            # Get changed files via git status --porcelain
            try:
                raw = git._run(["status", "--porcelain", "-uall", "--no-renames"])
                changed = _parse_git_porcelain(raw)
            except Exception:
                changed = []

            # Get +/- counts for modified files
            for f in changed:
                if f["status"] in ("modified", "added", "deleted"):
                    try:
                        # git diff --numstat HEAD -- <path> for modified files
                        if f["status"] == "untracked":
                            # Count all lines as additions
                            file_path = work_dir / f["path"]
                            if await aexists(file_path):
                                content = await aread_text(file_path)
                                lines = content.split("\n")
                                if lines and lines[-1] == "":
                                    lines.pop()
                                f["additions"] = len(lines)
                        elif f["status"] == "added":
                            # git diff --cached --numstat HEAD -- <path>
                            try:
                                numstat = git._run(
                                    ["diff", "--cached", "--numstat", "HEAD", "--", f["path"]]
                                )
                                parts = numstat.strip().split("\t")
                                f["additions"] = int(parts[0]) if parts[0] != "-" else 0
                                f["deletions"] = int(parts[1]) if len(parts) > 1 and parts[1] != "-" else 0
                            except Exception:
                                # Fallback: count from file content
                                if await aexists(work_dir / f["path"]):
                                    content = await aread_text(work_dir / f["path"])
                                    lines = content.split("\n")
                                    if lines and lines[-1] == "":
                                        lines.pop()
                                    f["additions"] = len(lines)
                        else:
                            try:
                                numstat = git._run(
                                    ["diff", "--numstat", "HEAD", "--", f["path"]]
                                )
                                parts = numstat.strip().split("\t")
                                f["additions"] = int(parts[0]) if parts[0] != "-" else 0
                                f["deletions"] = int(parts[1]) if len(parts) > 1 and parts[1] != "-" else 0
                            except Exception:
                                pass
                    except Exception:
                        pass

            result["changedFiles"] = changed
            return result
    except Exception as exc:
        logger.debug("git status failed: %s", exc)

    # ── 2. Fallback: session file changes from DB ──────────────────
    if session_id:
        try:
            changes = await _get_session_file_changes(session_id, work_dir)
            if changes:
                result["changedFiles"] = [
                    {"path": c["path"], "status": c["status"], "additions": c["additions"], "deletions": c["deletions"]}
                    for c in changes
                ]
                return result
        except Exception as exc:
            logger.debug("session file changes failed: %s", exc)

    # ── 3. File system scan (workspace dir files) ──────────────────
    result["state"] = "not_git_repo"
    return result


@router.get("/diff")
async def get_workspace_diff(
    path: str = Query(..., description="File path relative to workspace root"),
    session_id: str = Query("", alias="sessionId"),
    user: dict = Depends(get_current_user),
):
    """Return a unified diff for a single file.

    Priority:
    1. Session diff (from tool-call history in DB messages) — most relevant
    2. Git diff HEAD -- <path> — if workspace is a git repo
    3. Synthetic diff comparing empty vs current content — fallback
    """
    user_id = user["id"]

    # Resolve the workspace root
    from app.services.workspace_context import get_workspace_root
    work_dir = get_workspace_root()

    resolved_path = _safe_resolve(work_dir, path)
    if resolved_path is None:
        raise HTTPException(status_code=400, detail=f"Invalid path: {path}")

    # ── 1. Try session file change diff ────────────────────────────
    if session_id:
        try:
            changes = await _get_session_file_changes(session_id, work_dir)
            change = next((c for c in changes if c["path"] == path), None)
            if change and change.get("diff"):
                return {
                    "state": "ok",
                    "path": path,
                    "diff": change["diff"],
                }
        except Exception:
            pass

    # ── 2. Try git diff ───────────────────────────────────────────
    is_git = (work_dir / ".git").exists()
    if is_git:
        try:
            from app.services.git_service import GitService
            git = GitService(work_dir)

            # Check if file exists (for untracked files, git diff won't work)
            if not await aexists(resolved_path):
                raise HTTPException(status_code=404, detail=f"File not found: {path}")

            try:
                # Try to get diff against HEAD
                diff_output = git._run([
                    "diff", "--no-ext-diff", "--find-renames",
                    "HEAD", "--", path,
                ])
                if diff_output:
                    # Truncate if too large
                    lines = diff_output.split("\n")
                    if len(lines) > MAX_DIFF_LINES or len(diff_output) > MAX_DIFF_CHARS:
                        diff_output = "\n".join(lines[:MAX_DIFF_LINES])
                        diff_output = diff_output[:MAX_DIFF_CHARS]
                    return {"state": "ok", "path": path, "diff": diff_output}
            except Exception:
                pass

            # If file is untracked, build synthetic diff
            try:
                content = await aread_text(resolved_path)
                return {
                    "state": "ok",
                    "path": path,
                    "diff": _build_synthetic_diff("/dev/null", path, "", content),
                }
            except Exception:
                raise HTTPException(status_code=404, detail=f"Cannot read file: {path}")
        except Exception as exc:
            logger.warning("git diff failed for %s: %s", path, exc)

    # ── 3. Fallback: simple synthetic diff ─────────────────────────
    if await aexists(resolved_path):
        try:
            content = await aread_text(resolved_path)
            return {
                "state": "ok",
                "path": path,
                "diff": _build_synthetic_diff("/dev/null", path, "", content),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Read error: {exc}")

    raise HTTPException(status_code=404, detail=f"File not found: {path}")


# ── Helper: git status parsing ──────────────────────────────────────

def _parse_git_porcelain(raw: str) -> list[dict]:
    """Parse ``git status --porcelain -uall --no-renames`` output."""
    results: list[dict] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        # Format: XY filename
        # XY = status code (2 chars), then space, then filename
        if len(line) < 4:
            continue
        xy = line[:2].strip()
        fpath = line[3:].strip().strip('"')
        if not fpath:
            continue

        # Map porcelain status codes
        status_map = {
            "M": "modified",
            "A": "added",
            "D": "deleted",
            "R": "renamed",
            "C": "copied",
            "??": "untracked",
            "!!": "ignored",
        }
        # XY can be: " M" (unstaged modified), "M " (staged modified), "??" (untracked), etc.
        if xy.startswith("?"):
            status = "untracked"
        elif "D" in xy:
            status = "deleted"
        elif "A" in xy:
            status = "added"
        elif "R" in xy:
            status = "renamed"
        elif "M" in xy:
            status = "modified"
        else:
            status = status_map.get(xy, "unknown")

        results.append({
            "path": fpath,
            "status": status,
            "additions": 0,
            "deletions": 0,
        })

    # Sort: modified first, then added, then deleted, then untracked, alphabetically within each group
    status_order = {"modified": 0, "added": 1, "deleted": 2, "renamed": 3, "untracked": 4, "unknown": 5}
    results.sort(key=lambda f: (status_order.get(f["status"], 99), f["path"].lower()))
    return results


# ── Helper: session file changes from DB messages ───────────────────

async def _get_session_file_changes(session_id: str, workspace_root: Path) -> list[dict]:
    """Extract file changes from Write/Edit tool calls in the session's messages."""
    from app.db.session import afetch_all
    import json

    try:
        messages = await afetch_all(
            "SELECT sender, content, type, created_at "
            "FROM messages WHERE session_id=$1 AND type='tool_use' "
            "ORDER BY created_at ASC",
            session_id,
        )
    except Exception:
        return []

    changes: dict[str, dict] = {}

    for msg in messages:
        try:
            body = msg.get("content", "")
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except (json.JSONDecodeError, TypeError):
                    continue

            if not isinstance(body, dict):
                continue

            tool_name = body.get("name", body.get("tool_name", ""))
            tool_input = body.get("input", body.get("arguments", {}))

            if isinstance(tool_input, str):
                try:
                    tool_input = json.loads(tool_input)
                except (json.JSONDecodeError, TypeError):
                    continue

            if not isinstance(tool_input, dict):
                continue

            file_path = tool_input.get("file_path", tool_input.get("path", ""))
            if not file_path:
                continue

            # Normalize path — strip workspace prefix
            rel_path = _relative_path(file_path, workspace_root)

            if tool_name in ("Write", "file_write"):
                content = tool_input.get("content", "")
                if not isinstance(content, str):
                    content = str(content)

                existing = changes.get(rel_path)
                if existing:
                    existing["new_content"] = content
                    existing["status"] = "modified"
                    existing["additions"] = len(content.split("\n"))
                else:
                    changes[rel_path] = {
                        "path": rel_path,
                        "status": "added",
                        "old_content": "",
                        "new_content": content,
                        "additions": len(content.split("\n")),
                        "deletions": 0,
                    }

            elif tool_name in ("Edit", "file_patch"):
                old_str = tool_input.get("old_string", "")
                new_str = tool_input.get("new_string", "")
                if not isinstance(old_str, str):
                    old_str = str(old_str)
                if not isinstance(new_str, str):
                    new_str = str(new_str)

                existing = changes.get(rel_path)
                if existing:
                    # Apply the edit to existing new_content
                    current = existing.get("new_content", existing.get("old_content", ""))
                    updated = current.replace(old_str, new_str, 1)
                    existing["new_content"] = updated
                    old_lines = len(current.split("\n"))
                    new_lines = len(updated.split("\n"))
                    existing["additions"] = max(0, new_lines - old_lines)
                    existing["deletions"] = max(0, old_lines - new_lines)
                    existing["status"] = "modified"
                else:
                    changes[rel_path] = {
                        "path": rel_path,
                        "status": "modified",
                        "old_content": old_str,
                        "new_content": new_str,
                        "additions": max(0, len(new_str.split("\n")) - len(old_str.split("\n"))),
                        "deletions": max(0, len(old_str.split("\n")) - len(new_str.split("\n"))),
                    }
        except Exception:
            continue

    # Build diffs for each change
    for c in changes.values():
        old = c.get("old_content", "")
        new = c.get("new_content", old)
        if old != new:
            c["diff"] = _build_synthetic_diff(c["path"], c["path"], old, new)
        elif c["status"] == "added":
            c["diff"] = _build_synthetic_diff("/dev/null", c["path"], "", new)

    return sorted(changes.values(), key=lambda c: c["path"])


# ── Utility: synthetic unified diff ──────────────────────────────────

def _build_synthetic_diff(
    old_path: str, new_path: str, old_content: str, new_content: str,
) -> str:
    """Build a unified diff string with difflib (Python stdlib).

    Mimics the ``buildSyntheticDiff`` function from the reference TypeScript
    implementation, but uses Python's built-in difflib.unified_diff.
    """
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{old_path}" if old_path != "/dev/null" else old_path,
            tofile=f"b/{new_path}" if new_path != "/dev/null" else new_path,
            lineterm="",
        )
    )

    # Truncate at max
    if len(diff_lines) > MAX_DIFF_LINES:
        diff_lines = diff_lines[:MAX_DIFF_LINES]
        diff_lines.append(f"... ({len(diff_lines)} more lines)")
    result = "\n".join(diff_lines)
    if len(result) > MAX_DIFF_CHARS:
        result = result[:MAX_DIFF_CHARS]
    return result


# ── Utility: path helpers ────────────────────────────────────────────

def _safe_resolve(base: Path, path: str) -> Optional[Path]:
    """Resolve a path and ensure it stays within the base directory."""
    try:
        resolved = (base / path).resolve()
        if not str(resolved).startswith(str(base.resolve())):
            return None
        return resolved
    except (OSError, ValueError, RuntimeError):
        return None


def _relative_path(absolute_path: str, workspace_root: Path) -> str:
    """Convert an absolute path to a workspace-relative one."""
    try:
        p = Path(absolute_path).resolve()
        root = workspace_root.resolve()
        rel = p.relative_to(root)
        return str(rel).replace("\\", "/")
    except (ValueError, OSError):
        # If not under workspace root, return as-is
        return Path(absolute_path).name


# ── Export ──────────────────────────────────────────────────────────

__all__ = ["router"]
