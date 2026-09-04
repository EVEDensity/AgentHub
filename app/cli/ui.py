"""Rich rendering layer for the developer CLI (Claude Code-style UX).

Design contract (docs/roadmaps/north-star-developer-cli-experience.md):

- dark-mode theme: primary info bright white, secondary info (paths,
  timestamps) muted grey, success green, danger/warn red/yellow, tool
  calls blue, AI thinking purple
- visual blocks: bordered panels isolate AI thinking / tool execution /
  final output; code changes render as a git diff
- feedback: live spinner + elapsed timer + streaming status events, so
  long missions never look frozen
- human-in-the-loop: side-effect confirm menu (Yes / No / Always allow)
- session footer: weak-text cost summary (missions / artifacts / elapsed)

Every entry point degrades gracefully: when the caller passes a plain
``emit`` (test seam) or the terminal is not a TTY, callers fall back to
plain strings, so the existing test contract (input_fn/output_fn) keeps
working unchanged.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from rich.box import ROUNDED
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text

# ── Theme ──────────────────────────────────────────────────────────────

C_PRIMARY = "bright_white"  # 主信息
C_MUTED = "grey62"  # 次要信息：路径、时间戳
C_SUCCESS = "green3"
C_DANGER = "red3"
C_WARN = "yellow3"
C_TOOL = "deep_sky_blue3"  # 工具调用
C_ACCENT = "medium_purple3"  # AI 思考

STYLE_PRIMARY = Style(color=C_PRIMARY)
STYLE_MUTED = Style(color=C_MUTED)
STYLE_TOOL = Style(color=C_TOOL)
STYLE_ACCENT = Style(color=C_ACCENT)

STATUS_COLOR = {
    "SUCCEEDED": C_SUCCESS,
    "FAILED": C_DANGER,
    "RUNNING": C_TOOL,
    "PENDING": C_MUTED,
    "TIMEOUT": C_WARN,
}

_CONFIRM_YES = ("y", "yes", "1")
_CONFIRM_NO = ("n", "no", "2")
_CONFIRM_ALWAYS = ("a", "always", "3")


# ── Git context ───────────────────────────────────────────────────────


def _git(root: Path, *args: str) -> str | None:
    """Run one git query; None when git is absent or not a repo."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def git_branch(root: Path) -> str | None:
    out = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if out else None


def git_diff_text(root: Path, max_lines: int = 240) -> str | None:
    """Unified diff of tracked changes; None when clean / not a repo."""
    diff = _git(root, "diff", "--color=never")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    if not diff and not untracked:
        return None
    chunks: list[str] = []
    if diff:
        chunks.append(diff.rstrip("\n"))
    if untracked:
        names = [n for n in untracked.splitlines() if n.strip()]
        if names:
            listed = "\n".join(f"+ {n}" for n in names[:20])
            more = "" if len(names) <= 20 else f"\n+ … (+{len(names) - 20} more)"
            chunks.append(f"--- /dev/null (untracked)\n{listed}{more}")
    text = "\n".join(chunks)
    lines = text.splitlines()
    if len(lines) > max_lines:
        text = "\n".join(lines[:max_lines]) + f"\n… (+{len(lines) - max_lines} lines)"
    return text


def git_changed_files(root: Path) -> list[str]:
    """Return tracked and untracked paths changed in the worktree."""
    out = _git(root, "status", "--short")
    if not out:
        return []
    files: list[str] = []
    for line in out.splitlines():
        if len(line) > 3:
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path:
                files.append(path)
    return files


def git_restore_tracked(root: Path) -> bool:
    """Restore tracked worktree changes; untracked files are preserved."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "restore", "--worktree", "--", "."],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def git_restore_paths(root: Path, paths: list[str]) -> bool:
    """Restore only validated relative tracked paths; never delete untracked files."""
    safe = [p for p in paths if p and not Path(p).is_absolute() and ".." not in Path(p).parts]
    if not safe:
        return True
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "restore", "--worktree", "--", *safe],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def git_tracked_changed_files(root: Path) -> list[str]:
    """Return only tracked files with worktree or index changes."""
    names: list[str] = []
    for args in (("diff", "--name-only"), ("diff", "--cached", "--name-only")):
        out = _git(root, *args) or ""
        names.extend(line.strip() for line in out.splitlines() if line.strip())
    return list(dict.fromkeys(names))


def git_status_snapshot(root: Path) -> frozenset[str]:
    """Capture changed paths before a Mission starts."""
    return frozenset(git_changed_files(root))


def git_head_commit(root: Path) -> str | None:
    """Return the current HEAD commit, or None outside a Git repository."""
    out = _git(root, "rev-parse", "HEAD")
    return out.strip() if out else None


def git_changes_since(root: Path, before: frozenset[str]) -> list[str]:
    """Return paths changed after a baseline snapshot."""
    return sorted(set(git_changed_files(root)) - set(before))


# ── Header ─────────────────────────────────────────────────────────────


def render_header(
    cwd: Path,
    provider: str,
    model: str,
    workspace_root: Path,
) -> Panel:
    """Top header: working path + git branch + model channel."""
    branch = git_branch(cwd)
    line = Text()
    line.append(str(cwd), style=STYLE_MUTED)
    if branch:
        line.append(f"  ({branch})", style=C_ACCENT)
    line.append(f"  ·  {provider}/{model}", style=STYLE_PRIMARY)
    line.append(f"\nworkspace: {workspace_root}", style=STYLE_MUTED)
    return Panel(
        line,
        border_style=Style(color=C_ACCENT, dim=True),
        box=ROUNDED,
        padding=(0, 1),
    )


# ── Live spinner + elapsed timer ──────────────────────────────────────


class _StatusRenderable:
    """Re-computed each Live refresh: spinner + ticking elapsed clock."""

    def __init__(self, label: str) -> None:
        self._label = label
        self._t0 = time.monotonic()
        self._last_status = ""
        self._state_hint = ""

    def update_status(self, status: str) -> None:
        self._last_status = status

    def update_view_state(self, state: Any) -> None:
        from app.cli.reducer import state_summary
        self._state_hint = state_summary(state)

    def __rich_console__(self, console: Console, options: Any) -> Any:
        elapsed = time.monotonic() - self._t0
        spinner = Spinner("dots", Text(f" {self._label}", style=STYLE_TOOL))
        status_line = Text(
            f"  {elapsed:5.1f}s  {self._last_status or 'booting…'}{(' · ' + self._state_hint) if self._state_hint else ''}",
            style=STYLE_MUTED,
        )
        yield spinner
        yield status_line


class MissionRunner:
    """Context manager: live spinner + elapsed timer + status stream.

    Usage::

        with MissionRunner(console, "running · deepseek/chat") as runner:
            result = execute_objective(..., on_status=runner.on_status)
    """

    def __init__(self, console: Console, label: str) -> None:
        self._console = console
        self._label = label
        self._renderable = _StatusRenderable(label)
        self._live = Live(
            self._renderable,
            console=console,
            refresh_per_second=8,
            transient=True,
        )

    def on_status(self, status: str) -> None:
        self._renderable.update_status(status)

    def on_text(self, text: str) -> None:
        """Render assistant deltas without disturbing the live status line."""
        self._live.console.print(Text(text, style=STYLE_PRIMARY), end="")

    def on_view_state(self, state: Any) -> None:
        self._renderable.update_view_state(state)

    def __enter__(self) -> "MissionRunner":
        self._live.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._live.stop()


# ── Result panel ───────────────────────────────────────────────────────


def _status_style(status: str) -> str:
    return STATUS_COLOR.get(status.upper(), C_PRIMARY)


def render_result_panel(result: Any) -> Panel:
    """Bordered card for one finished mission (success/danger border)."""
    ok = str(result.status).upper() == "SUCCEEDED"
    border = C_SUCCESS if ok else C_DANGER
    head = Text()
    head.append("● ", style=border)
    head.append(str(result.status), style=border)
    head.append(f"  {result.mission_id}", style=STYLE_MUTED)
    head.append(f"  (exit {result.exit_code})", style=STYLE_MUTED)

    body = Text()
    body.append(
        f"{result.wall_seconds:.1f}s · {len(result.artifacts)} artifacts",
        style=STYLE_MUTED,
    )
    files = list(getattr(result, "workspace_files", None) or [])
    if files:
        preview = ", ".join(files[:6])
        more = (
            f" +{len(files) - 6} more" if len(files) > 6 else ""
        )
        body.append(f"\nfiles: {preview}{more}", style=STYLE_TOOL)

    return Panel(
        Group(head, body),
        title="mission",
        border_style=Style(color=border),
        box=ROUNDED,
        padding=(0, 1),
    )


def render_state_panel(state: Any) -> Panel:
    """Render canonical reducer state for stable terminal snapshots."""
    from app.cli.reducer import state_summary
    summary = state_summary(state) or "idle"
    body = Text(summary, style=STYLE_PRIMARY)
    if getattr(state, "assistant_text", ""):
        body.append(f"\ntext: {state.assistant_text}", style=STYLE_PRIMARY)
    if getattr(state, "diagnostics", ()):
        body.append("\n" + "\n".join(state.diagnostics), style=Style(color=C_WARN))
    return Panel(body, title="session state", border_style=Style(color=C_TOOL), box=ROUNDED, padding=(0, 1))


def render_diff_panel(root: Path, max_lines: int = 240) -> Panel | None:
    """Git-diff style highlight (green +/red -) of workspace changes."""
    text = git_diff_text(root, max_lines=max_lines)
    if not text:
        return None
    syntax = Syntax(text, "diff", theme="ansi_dark", word_wrap=False)
    return Panel(
        syntax,
        title="git diff",
        border_style=Style(color=C_TOOL, dim=True),
        box=ROUNDED,
        padding=(0, 1),
    )


# ── Human-in-the-loop confirm ─────────────────────────────────────────


def confirm_side_effect(
    console: Console,
    read_line: Callable[[str], str],
    objective: str,
) -> str:
    """Ask before a side-effect mission. Returns 'yes' / 'no' / 'always'.

    EOF or Ctrl+C at the prompt is treated as 'no' (fail-safe).
    """
    console.print(
        Panel(
            Text(objective, style=STYLE_PRIMARY),
            title="该任务将在工作区执行写入/命令",
            border_style=Style(color=C_WARN),
            box=ROUNDED,
            padding=(0, 1),
        )
    )
    while True:
        try:
            answer = read_line(
                "允许执行？ [1] Yes  [2] No  [3] Always allow > "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "no"
        if answer in _CONFIRM_YES:
            return "yes"
        if answer in _CONFIRM_NO:
            return "no"
        if answer in _CONFIRM_ALWAYS:
            return "always"
        console.print(
            "  请输入 1/2/3（Yes/No/Always）", style=STYLE_MUTED
        )


# ── Session cost footer ────────────────────────────────────────────────


def format_cost_line(session_records: list[dict[str, Any]]) -> RenderableType:
    """Weak-text footer: mission count / artifacts / elapsed / tokens.

    Token accounting depends on the adapter layer plumb usage back
    through checkpoints. When the backend does not yet report tokens the
    line is gracefully omitted (no misleading 0s).
    """
    missions = len(session_records)
    artifacts = sum(int(r.get("artifacts") or 0) for r in session_records)
    seconds = sum(float(r.get("wall_seconds") or 0.0) for r in session_records)
    tokens_total = sum(int(r.get("total_tokens") or 0) for r in session_records)
    tokens_prompt = sum(int(r.get("prompt_tokens") or 0) for r in session_records)
    tokens_completion = sum(int(r.get("completion_tokens") or 0) for r in session_records)
    cancelled = sum(1 for r in session_records if r.get("cancelled"))
    text = Text()
    text.append("  ⌁ ", style=STYLE_MUTED)
    text.append(f"{missions} mission", style=STYLE_MUTED)
    if missions != 1:
        text.append("s", style=STYLE_MUTED)
    text.append(f" · {artifacts} artifacts · {seconds:.1f}s", style=STYLE_MUTED)
    if tokens_total > 0:
        text.append(f" · tokens {tokens_total:,}", style=C_ACCENT)
        text.append(
            f" ({tokens_prompt:,} in / {tokens_completion:,} out)",
            style=STYLE_MUTED,
        )
    if cancelled > 0:
        text.append(f" · {cancelled} cancelled", style=C_WARN)
    return text
