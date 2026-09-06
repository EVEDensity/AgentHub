"""Changed-file disclosure for finished desktop tasks (G7).

After a ``desktop.task`` WorkUnit succeeds, the desktop UI shows which files
the task changed. The desktop local runner commits file writes to the git
repository inside its execution workspace. Changes are read back with a
read-only ``git diff`` — the same
workspace root the runner executes in, never a state source of its own.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.services.desktop_local_runner import (
    DESKTOP_RUNNER_LABEL,
    DESKTOP_WORKSPACE_ID,
    WORKSPACE_ROOT_ENV,
)
from app.services.workspace_context import build_workspace_root

# The diff disclosure is best-effort: any git failure (missing git, not a
# repository, unborn HEAD, timeout) yields an empty change set instead of
# surfacing a runner detail through the user-facing API.
GIT_SHOW_TIMEOUT_SECONDS = 30.0

_NUMSTAT_LINE = re.compile(
    r"^(?P<additions>\d+|-)\t(?P<deletions>\d+|-)\t(?P<path>.+)$"
)
_STATUS_LINE = re.compile(r"^(?P<status>[A-Z][0-9]*)\t(?P<rest>.+)$")
_RENAME_BRACE = re.compile(r"^(.*)\{([^{}]*) => ([^{}]*)\}(.*)$")


def resolve_desktop_execution_workspace_root(
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return the desktop runner execution workspace root.

    Mirrors ``DesktopLocalRunnerSettings.default_workspace_root``: the env
    override wins, otherwise the fixed ``local-admin/desktop-local-runner``
    directory under the standard workspace tree.
    """
    environment = os.environ if env is None else env
    configured = environment.get(WORKSPACE_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).resolve()
    return build_workspace_root(DESKTOP_WORKSPACE_ID, DESKTOP_RUNNER_LABEL)


def _git_show(workspace_root: Path, diff_format: str) -> str | None:
    """Run one read-only ``git show`` and return stdout, or ``None`` on failure."""
    try:
        completed = subprocess.run(
            ["git", "show", diff_format, "--format=", "HEAD"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_SHOW_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def collect_desktop_changed_files(workspace_root: Path) -> list[dict[str, Any]]:
    """Read the HEAD change set of the workspace git repository (read-only).

    Equivalent to ``git show --numstat --name-status HEAD``: git renders only
    one diff format per invocation, so --numstat and --name-status run as two
    read-only calls and are merged positionally (both walk the same diff in
    the same order). Returns ``[{path, status, additions, deletions}]``; an
    empty list when the directory is not a git repository or has no commit.
    """
    root = Path(workspace_root)
    if not (root / ".git").exists():
        return []
    numstat_output = _git_show(root, "--numstat")
    if numstat_output is None:
        return []
    status_output = _git_show(root, "--name-status")
    if status_output is None:
        status_output = ""
    return _merge_git_show_sections(
        _parse_numstat(numstat_output), _parse_name_status(status_output)
    )


def _parse_numstat(output: str) -> list[tuple[str, int, int]]:
    entries: list[tuple[str, int, int]] = []
    for line in output.splitlines():
        match = _NUMSTAT_LINE.match(line)
        if match is None:
            continue
        additions = match.group("additions")
        deletions = match.group("deletions")
        entries.append(
            (
                _normalize_diff_path(match.group("path")),
                0 if additions == "-" else int(additions),
                0 if deletions == "-" else int(deletions),
            )
        )
    return entries


def _parse_name_status(output: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in output.splitlines():
        match = _STATUS_LINE.match(line)
        if match is None:
            continue
        parts = line.split("\t")
        if len(parts) >= 3:  # rename/copy: status, old path, new path
            entries.append(
                (match.group("status"), _normalize_diff_path(parts[-1]))
            )
        else:
            entries.append(
                (match.group("status"), _normalize_diff_path(parts[1]))
            )
    return entries


def _merge_git_show_sections(
    numstat_entries: list[tuple[str, int, int]],
    status_entries: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Merge numstat and name-status entries for the same diff.

    Both sections describe the same diff in the same order, so entries are
    zipped by position; a path-based fallback keeps the result stable if the
    sections ever disagree.
    """
    files: list[dict[str, Any]] = []
    if len(numstat_entries) == len(status_entries):
        for (path, additions, deletions), (status, _path) in zip(
            numstat_entries, status_entries
        ):
            files.append(
                {
                    "path": path,
                    "status": status,
                    "additions": additions,
                    "deletions": deletions,
                }
            )
        return files
    status_by_path = {path: status for status, path in status_entries}
    for path, additions, deletions in numstat_entries:
        files.append(
            {
                "path": path,
                "status": status_by_path.get(path, "M"),
                "additions": additions,
                "deletions": deletions,
            }
        )
    return files


def _normalize_diff_path(raw: str) -> str:
    """Reduce a numstat rename path (``old => new``) to the new path."""
    path = raw.strip()
    if " => " not in path:
        return path
    brace = _RENAME_BRACE.match(path)
    if brace is not None:
        prefix, _old, new, suffix = brace.groups()
        return f"{prefix}{new}{suffix}"
    return path.split(" => ", 1)[1].strip()


__all__ = [
    "GIT_SHOW_TIMEOUT_SECONDS",
    "collect_desktop_changed_files",
    "resolve_desktop_execution_workspace_root",
]
