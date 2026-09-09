"""Rich rendering layer for the developer CLI (Claude Code-style UX).

Design contract (docs/roadmaps/north-star-developer-cli-experience.md):

- dark-mode Tech Blue theme: cold-white body text, muted blue-grey metadata,
  cyan success, red/yellow failure states, electric-blue tool calls, and
  ice-blue thinking
- visual blocks: lightweight rules and indentation isolate AI thinking /
  tool execution / final output; code changes render as a git diff
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

import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable
from dataclasses import dataclass

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markup import escape
from rich.rule import Rule
from rich.spinner import Spinner
from rich.style import Style
from rich.syntax import Syntax
from rich.theme import Theme
from rich.text import Text

# ── Theme ──────────────────────────────────────────────────────────────

C_PRIMARY = "#E2E8F0"  # 冷白正文
C_MUTED = "#64748B"  # 路径、时间戳、统计
C_SUCCESS = "#06B6D4"  # 青蓝成功态
C_DANGER = "#F87171"
C_WARN = "#FBBF24"
C_TOOL = "#3B82F6"  # 电光蓝工具态
C_ACCENT = "#0EA5E9"  # 冰蓝动态高亮
C_BRAND = "#2563EB"

STYLE_PRIMARY = Style(color=C_PRIMARY)
STYLE_MUTED = Style(color=C_MUTED)
STYLE_TOOL = Style(color=C_TOOL)
STYLE_ACCENT = Style(color=C_ACCENT)
STYLE_BRAND = Style(color=C_BRAND, bold=True)

TECH_BLUE_THEME = Theme(
    {
        "agenthub.primary": C_PRIMARY,
        "agenthub.muted": C_MUTED,
        "agenthub.brand": f"bold {C_BRAND}",
        "agenthub.tool": C_TOOL,
        "agenthub.accent": C_ACCENT,
        "agenthub.success": C_SUCCESS,
        "agenthub.warning": C_WARN,
    }
)


def make_console(**kwargs: Any) -> Console:
    """Create a Rich console with the shared Tech Blue semantic theme."""
    kwargs.setdefault("theme", TECH_BLUE_THEME)
    return Console(**kwargs)

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
_TOOL_ALLOW_ONCE = ("y", "yes", "1")
_TOOL_ALLOW_ATTEMPT = ("a", "attempt")
_TOOL_ALLOW_SESSION = ("s", "session")
_TOOL_EDIT = ("e", "edit")
_TOOL_DENY = ("n", "no", "2")


@dataclass(frozen=True)
class ToolApprovalRequest:
    """Structured metadata shown before a side-effecting tool executes."""

    tool_name: str
    path: str = ""
    expected_sha256: str = ""
    action: str = ""
    command: str = ""
    url: str = ""
    diff_preview: str = ""
    risk: str = ""


def render_tool_approval(request: ToolApprovalRequest) -> RenderableType:
    """Render a narrow, metadata-only approval card with optional diff."""
    lines = [Text(f"[Tool: {request.tool_name}]", style=STYLE_BRAND)]
    if request.path:
        lines.append(Text(f"Path: {request.path}", style=STYLE_PRIMARY))
    if request.expected_sha256:
        lines.append(Text(f"Expected SHA: {request.expected_sha256[:16]}", style=STYLE_MUTED))
    if request.action:
        lines.append(Text(f"Action: {request.action}", style=STYLE_PRIMARY))
    if request.command:
        lines.append(Text(f"Command: {request.command[:240]}", style=STYLE_PRIMARY))
    if request.url:
        lines.append(Text(f"URL: {request.url[:240]}", style=STYLE_PRIMARY))
    if request.risk:
        lines.append(Text(f"Risk: {request.risk[:240]}", style=Style(color=C_WARN)))
    if request.diff_preview:
        lines.append(Text("Diff preview:", style=STYLE_ACCENT))
        for line in request.diff_preview.splitlines()[:80]:
            lines.append(Text(f"│ {line[:240]}", style=STYLE_MUTED))
    lines.append(Text("[y] Allow Once  [a] Allow for Attempt  [s] Allow for Session  [e] Edit Command  [n] Deny", style=STYLE_ACCENT))
    return Group(*lines)


def confirm_tool_approval(
    console: Console,
    read_line: Callable[[str], str],
    request: ToolApprovalRequest,
    *,
    is_tty: bool = True,
) -> str:
    """Return one of ``once``, ``attempt``, ``session``, ``edit``, ``deny``.

    Non-interactive callers fail closed without reading stdin.
    """
    if not is_tty:
        return "deny"
    console.print(render_tool_approval(request))
    while True:
        try:
            answer = read_line("approval > " ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "deny"
        if answer in _TOOL_ALLOW_ONCE:
            return "once"
        if answer in _TOOL_ALLOW_ATTEMPT:
            return "attempt"
        if answer in _TOOL_ALLOW_SESSION:
            return "session"
        if answer in _TOOL_EDIT:
            return "edit"
        if answer in _TOOL_DENY:
            return "deny"
        console.print("  choose y/a/s/e/n", style=STYLE_MUTED)


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
) -> RenderableType:
    """Compact Claude-Code-style header without a surrounding box."""
    branch = git_branch(cwd)
    line = Text()
    line.append("✦ AgentHub", style=STYLE_BRAND)
    line.append(" v1.0.0", style=STYLE_MUTED)
    if branch:
        line.append(" │ ", style=STYLE_MUTED)
        line.append(f"🌿 {branch}", style=STYLE_ACCENT)
    line.append(" │ ", style=STYLE_MUTED)
    line.append(f"🌐 {provider}/{model}", style=STYLE_PRIMARY)
    line.append(" │ ", style=STYLE_MUTED)
    line.append("📁 workspace=", style=STYLE_ACCENT)
    line.append(str(workspace_root), style=STYLE_MUTED)
    return Group(line, Rule(style=Style(color=C_MUTED, dim=True)))


# ── Live spinner + elapsed timer ──────────────────────────────────────


class _StatusRenderable:
    """Re-computed each Live refresh: spinner + ticking elapsed clock."""

    def __init__(self, label: str) -> None:
        self._label = label
        self._t0 = time.monotonic()
        self._last_status = ""
        self._state_hint = ""
        self._tool_hint = ""
        self._tool_result = ""
        self._assistant_text = ""

    def update_status(self, status: str) -> None:
        self._last_status = status

    def update_view_state(self, state: Any) -> None:
        from app.cli.reducer import render_snapshot, state_summary
        self._state_hint = state_summary(state)
        snapshot = render_snapshot(state)
        tools = snapshot.get("tools") if isinstance(snapshot, dict) else None
        if tools:
            latest = tools[-1]
            if isinstance(latest, dict):
                self.update_tool(
                    str(latest.get("name") or "tool"),
                    str(latest.get("output") or "") if latest.get("status") in {"completed", "output"} else "",
                )

    def update_snapshot(self, snapshot: Any) -> str:
        """Consume the canonical JSON snapshot and return new text delta."""
        if not isinstance(snapshot, dict):
            return ""
        text = str(snapshot.get("assistantText") or "")
        delta = text[len(self._assistant_text):] if text.startswith(self._assistant_text) else text
        self._assistant_text = text
        self._state_hint = ""
        status = str(snapshot.get("status") or "")
        tools = snapshot.get("tools")
        if status:
            self._state_hint = status
        if isinstance(tools, list) and tools:
            latest = tools[-1]
            if isinstance(latest, dict):
                self.update_tool(str(latest.get("name") or "tool"), str(latest.get("output") or "") if latest.get("status") in {"completed", "output"} else "")
        return delta

    def update_tool(self, label: str, result: str = "") -> None:
        self._tool_hint = label
        self._tool_result = result

    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    def __rich_console__(self, console: Console, options: Any) -> Any:
        elapsed = time.monotonic() - self._t0
        label = (
            f"Executing: {self._tool_hint}..."
            if self._tool_hint
            else f"Thinking ({elapsed:.1f}s)..."
        )
        spinner = Spinner("dots", Text(f" {label}", style=STYLE_TOOL))
        status_line = Text(
            f"  {self._last_status or 'working'}{(' · ' + self._state_hint) if self._state_hint else ''}",
            style=STYLE_MUTED,
        )
        yield spinner
        yield status_line
        if self._tool_result:
            yield Text(f"  ✔ {self._tool_result}", style=STYLE_ACCENT)


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
        self._closed = False
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

    def on_snapshot(self, snapshot: Any) -> None:
        """Render only the canonical reducer snapshot projection."""
        delta = self._renderable.update_snapshot(snapshot)
        if delta:
            self.on_text(delta)

    def __enter__(self) -> "MissionRunner":
        self._live.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.finish()

    def finish(self, tokens: int | None = None) -> None:
        if self._closed:
            return
        self._live.stop()
        self._closed = True
        elapsed = self._renderable.elapsed()
        token_text = f" ({tokens:,} tokens)" if tokens is not None and tokens > 0 else ""
        self._console.print(Text(f"✢ Thought for {elapsed:.1f}s{token_text}", style=STYLE_MUTED))


class ThinkingFilter:
    """Incrementally hide model ``<think>`` blocks from the main transcript.

    Thinking is retained for an optional summary, but normal conversation only
    renders a compact collapsed marker once the block closes. Tags may be
    split across SSE chunks.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._thinking = False
        self._chars = 0
        self._thinking_text: list[str] = []
        self._dsml = False
        self._reported = False

    def feed(self, text: str) -> str:
        self._buffer += str(text)
        out: list[str] = []
        while self._buffer:
            if self._thinking:
                end = self._buffer.find("</think>")
                if end < 0:
                    self._chars += len(self._buffer)
                    self._thinking_text.append(self._buffer)
                    self._buffer = ""
                    break
                self._chars += end
                self._thinking_text.append(self._buffer[:end])
                self._buffer = self._buffer[end + len("</think>"):]
                self._thinking = False
                if not self._reported:
                    out.append(f"\n✢ Thought stream collapsed · thinking hidden ({self._chars} chars)\n")
                    self._reported = True
                continue
            if self._dsml:
                end = re.search(r"</[^>]*DSML[^>]*>", self._buffer)
                if end is None:
                    self._chars += len(self._buffer)
                    self._buffer = ""
                    break
                self._chars += end.end()
                self._buffer = self._buffer[end.end():]
                self._dsml = False
                continue
            opening = re.search(r"<[^>]*DSML[^>]*>", self._buffer)
            if opening is not None:
                out.append(self._buffer[:opening.start()])
                self._buffer = self._buffer[opening.end():]
                self._dsml = True
                continue
            start = self._buffer.find("<think>")
            if start < 0:
                keep = 32
                if len(self._buffer) <= keep:
                    break
                out.append(self._buffer[:-keep])
                self._buffer = self._buffer[-keep:]
                break
            out.append(self._buffer[:start])
            self._buffer = self._buffer[start + len("<think>"):]
            self._thinking = True
        return "".join(out)

    def flush(self) -> str:
        if self._thinking:
            self._chars += len(self._buffer)
            self._thinking_text.append(self._buffer)
            self._buffer = ""
            if not self._reported:
                self._reported = True
                return f"\n✢ Thought stream collapsed · thinking hidden ({self._chars} chars)\n"
            return ""
        text = self._buffer
        self._buffer = ""
        return text

    @property
    def thinking_text(self) -> str:
        return "".join(self._thinking_text)


# ── Result panel ───────────────────────────────────────────────────────


def _status_style(status: str) -> str:
    return STATUS_COLOR.get(status.upper(), C_PRIMARY)


def render_result_panel(result: Any) -> RenderableType:
    """Lightweight result summary using separators instead of box borders."""
    ok = str(result.status).upper() == "SUCCEEDED"
    border = C_SUCCESS if ok else C_DANGER
    head = Text()
    head.append("✔ " if ok else "✕ ", style=border)
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

    return Group(Rule("mission", style=Style(color=border)), head, body)


def render_state_panel(state: Any) -> RenderableType:
    """Render canonical reducer state for stable terminal snapshots."""
    from app.cli.reducer import state_summary, render_snapshot
    snapshot = render_snapshot(state)
    summary = state_summary(state) or "idle"
    body = Text(summary, style=STYLE_PRIMARY)
    if snapshot.get("assistantText"):
        body.append(f"\ntext: {snapshot['assistantText']}", style=STYLE_PRIMARY)
    if snapshot.get("diagnostics"):
        body.append("\n" + "\n".join(snapshot["diagnostics"]), style=Style(color=C_WARN))
    return Group(Text("│ ", style=STYLE_MUTED) + body)


def render_diff_panel(root: Path, max_lines: int = 240) -> RenderableType | None:
    """Git-diff style highlight (green +/red -) of workspace changes."""
    text = git_diff_text(root, max_lines=max_lines)
    if not text:
        return None
    syntax = Syntax(text, "diff", theme="ansi_dark", word_wrap=False)
    return Group(Rule("git diff", style=Style(color=C_TOOL, dim=True)), syntax)


def render_tool_started(tool: str, detail: str = "") -> Text:
    """Render a compact live-tool line for event-driven callers."""
    suffix = f" ({detail})" if detail else ""
    return Text(f"⠋ Executing: {tool}{suffix}...", style=STYLE_TOOL)


def render_tool_completed(summary: str, *, elapsed_ms: int | None = None) -> Text:
    suffix = f" ({elapsed_ms}ms)" if elapsed_ms is not None else ""
    return Text(f"✔ {summary}{suffix}", style=STYLE_ACCENT)


def render_artifact_summary(result: Any) -> RenderableType:
    """Render created/changed files as an indented artifact summary."""
    files = list(getattr(result, "workspace_files", None) or [])
    artifacts = list(getattr(result, "artifacts", None) or [])
    if not files and not artifacts:
        return Text("✢ No artifacts", style=STYLE_MUTED)
    lines: list[Text] = [Text("✦ Artifacts", style=STYLE_BRAND)]
    named_artifacts = [item for item in artifacts if isinstance(item, dict) and (item.get("name") or item.get("path"))]
    if named_artifacts or not files:
        items = [item for item in (named_artifacts or artifacts) if isinstance(item, dict)][:20]
        for index, item in enumerate(items):
            name = str(item.get("name") or item.get("path") or item.get("kind") or "artifact")
            size = item.get("size_bytes") or item.get("size")
            size_label = f" ({int(size) / 1024:.1f} KB)" if isinstance(size, (int, float)) and size else ""
            prefix = "└ " if index == len(items) - 1 and not (item.get("styles") or item.get("action")) else "├ "
            lines.append(Text(f"{prefix}📄 {escape(name)}{size_label}", style=STYLE_ACCENT))
            if item.get("styles") or item.get("style"):
                lines.append(Text(f"│ Styles: {escape(str(item.get('styles') or item.get('style')))}", style=STYLE_MUTED))
            if item.get("action"):
                lines.append(Text(f"└ Action: {escape(str(item['action']))}", style=STYLE_ACCENT))
        if len(artifacts) > 20:
            lines.append(Text(f"└ … +{len(artifacts) - 20} more", style=STYLE_MUTED))
    else:
        for index, name in enumerate(files[:20]):
            prefix = "└ " if index == min(len(files), 20) - 1 else "├ "
            lines.append(Text(f"{prefix}📄 {escape(str(name))}", style=STYLE_ACCENT))
        if len(files) > 20:
            lines.append(Text(f"└ … +{len(files) - 20} more", style=STYLE_MUTED))
    return Group(*lines)


def classify_workspace(root: Path, *, max_files: int = 5000) -> dict[str, Any]:
    """Walk the real workspace and return deterministic category counts."""
    ignored = {".git", ".agenthub", "node_modules", "__pycache__", ".venv", "venv", "target"}
    categories: dict[str, int] = {}
    extensions: dict[str, int] = {}
    directories: set[str] = set()
    total = 0
    truncated = False
    for path in root.rglob("*"):
        if total >= max_files:
            truncated = True
            break
        if not path.is_file() or any(part in ignored for part in path.relative_to(root).parts):
            continue
        total += 1
        ext = path.suffix.lower() or "[no extension]"
        extensions[ext] = extensions.get(ext, 0) + 1
        category = {
            ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
            ".ts": "TypeScript", ".tsx": "TypeScript", ".rs": "Rust", ".go": "Go",
            ".md": "Docs", ".json": "Config", ".yml": "Config", ".yaml": "Config",
            ".css": "Styles", ".html": "Web", ".sql": "Database",
        }.get(ext, "Other")
        categories[category] = categories.get(category, 0) + 1
        directories.add(str(path.parent.relative_to(root)) if path.parent != root else ".")
    return {"root": str(root), "total": total, "truncated": truncated, "categories": dict(sorted(categories.items())), "extensions": dict(sorted(extensions.items())), "directories": sorted(directories)}


def render_workspace_classification(root: Path) -> RenderableType:
    report = classify_workspace(root)
    count_label = f"at least {report['total']}" if report["truncated"] else str(report["total"])
    lines = [Text(f"✦ Workspace · {report['root']}", style=STYLE_BRAND), Text(f"✔ Found {count_label} files across {len(report['directories'])} directories", style=STYLE_ACCENT)]
    for category, count in report["categories"].items():
        lines.append(Text(f"│ {category:<12} {count:>4}", style=STYLE_PRIMARY))
    return Group(*lines)


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
        Group(
            Rule("permission", style=Style(color=C_WARN)),
            Text("该任务将在工作区执行写入/命令", style=Style(color=C_WARN, bold=True)),
            Text(objective, style=STYLE_PRIMARY),
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
