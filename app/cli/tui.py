"""Full-screen TUI for the developer CLI (north-star M2).

``python -m app.cli tui`` opens a Textual application over the same
engine as run/exec/chat: isolated SQLite mission-control + desktop
runner + verifier gate. Layout:

- header: model channel + running state
- transcript log: streaming per-turn status + result summaries
- input line: objectives and slash commands
- status bar: chained mission + session counters

Missions run in a thread worker so the UI stays responsive; status
callbacks marshal back through ``call_from_thread``. Slash commands
mirror the chat REPL (/help /missions /resume /unresume /new /status
/quit). Each finished mission is chained as the next turn's read-only
resume context — the same honest accumulation rule as chat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog

from app.cli.main import _load_config
from app.cli.runtime import (
    DEFAULT_MAX_TOTAL_TOKENS,
    DEFAULT_MISSION_TIMEOUT,
    DEFAULT_RUNNER_TIMEOUT_SECONDS,
    CliModelSettings,
    collect_agents_md_layers,
    execute_objective,
    list_recent_missions,
    merge_project_instructions,
    resolve_model_settings,
    state_dir,
)
from app.cli.reducer import SessionViewState

_PROMPT_PREFIX = "agenthub"
_HELP_LINES = (
    "/help          显示本帮助",
    "/missions      列出本地历史任务",
    "/resume <id>   将指定任务链入下一轮上下文",
    "/unresume      清除链式上下文",
    "/new           开始全新会话（清除链式上下文）",
    "/status        显示当前会话设置",
    "/cost          显示本会话成本摘要",
    "/context       查看当前上下文与 token 使用",
    "/quit          退出",
)


@dataclass
class TuiSessionState:
    """Mutable per-session state (chain + counters)."""

    chained_mission_id: str | None = None
    session_missions: list[str] = field(default_factory=list)
    session_records: list[dict[str, Any]] = field(default_factory=list)
    running: bool = False


class AgentHubTUI(App[None]):
    """Full-screen interactive surface over the mission engine."""

    TITLE = "AgentHub"
    CSS = """
    RichLog { border: round $primary; }
    Input { border: round $accent; }
    """
    BINDINGS = [
        Binding("ctrl+c", "quit", "退出", priority=True),
    ]
    ENABLE_COMMAND_PALETTE = False
    AUTO_FOCUS = "Input"

    def __init__(
        self,
        *,
        cwd: Path,
        provider: str | None,
        model: str | None,
        base_url: str | None,
        workspace: Path | None,
        mission_timeout: float,
        max_total_tokens: int,
        runner_timeout_seconds: float,
        no_web_search: bool,
        execute_fn: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__()
        self._cwd = cwd
        self._provider = provider
        self._model = model
        self._base_url = base_url
        self._workspace = workspace
        self._mission_timeout = mission_timeout
        self._max_total_tokens = max_total_tokens
        self._runner_timeout_seconds = runner_timeout_seconds
        self._no_web_search = no_web_search
        # Test seam: replace the engine call without thread tricks.
        self._execute = execute_fn or execute_objective
        self.session = TuiSessionState()
        self._settings: CliModelSettings | None = None
        self._directory: Path | None = None
        self._project_instructions = ""
        self._instruction_paths: list[Path] = []

    # ── layout ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(markup=False, wrap=True, highlight=False)
        yield Input(placeholder="输入任务目标或 /help 查看命令")
        yield Footer()

    # ── lifecycle ─────────────────────────────────────────────────────

    def on_mount(self) -> None:
        config = _load_config(self._cwd)
        self._settings = resolve_model_settings(
            provider=self._provider,
            model=self._model,
            base_url=self._base_url,
            config=config,
        )
        self._directory = state_dir(self._cwd)
        self._directory.mkdir(parents=True, exist_ok=True)
        workspace_root = self._workspace or self._cwd
        self._instruction_paths = collect_agents_md_layers(
            workspace_root, self._cwd
        )
        self._project_instructions = merge_project_instructions(
            self._instruction_paths
        )
        log_widget = self.query_one(RichLog)
        log_widget.write(
            "AgentHub TUI — engine: desktop runner + verifier gate"
        )
        log_widget.write(
            f"model: {self._settings.provider} / {self._settings.model}   "
            f"workspace: {workspace_root}"
        )
        if self._instruction_paths:
            log_widget.write(
                "agents.md: "
                + ", ".join(
                    str(p.parent.name) or "/" for p in self._instruction_paths
                )
            )
        log_widget.write("输入任务目标运行；/help 查看命令。")
        self.sub_title = f"{self._settings.provider}/{self._settings.model}"

    # ── input handling ────────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        objective = event.value.strip()
        event.input.value = ""
        if not objective:
            return
        if objective.startswith("/"):
            await self._handle_slash(objective)
            return
        if self.session.running:
            self._log("× 已有任务在运行，请等待完成")
            return
        self._log(f"{_PROMPT_PREFIX}> {objective}")
        await self._run_mission(objective)

    async def _handle_slash(self, command: str) -> None:
        parts = command.split()
        name = parts[0].lower()
        args = parts[1:]
        if name in ("/quit", "/exit", "/q"):
            self.exit()
            return
        if name == "/help":
            for line in _HELP_LINES:
                self._log(line)
            return
        if name == "/status":
            self._log(
                f"model: {self._settings.provider} / {self._settings.model}\n"
                f"workspace: {self._workspace or self._cwd}\n"
                f"state: {self._directory}\n"
                f"chained mission: {self.session.chained_mission_id or '（无）'}\n"
                f"session missions: {len(self.session.session_missions)}"
            )
            return
        if name == "/cost":
            from app.cli.ui import format_cost_line
            if not self.session.session_records:
                self._log("本会话尚未运行任务")
            else:
                self._log(format_cost_line(self.session.session_records))
            return
        if name == "/context":
            latest = self.session.session_records[-1] if self.session.session_records else {}
            self._log(
                f"chained mission: {self.session.chained_mission_id or '（无）'}\n"
                f"missions: {len(self.session.session_records)}\n"
                f"tokens: {int(latest.get('total_tokens') or 0):,} (latest)"
            )
            return
        if name in ("/new", "/unresume"):
            self.session.chained_mission_id = None
            self._log("已清除链式上下文")
            return
        if name == "/resume":
            if not args:
                self._log("用法: /resume <mission_id>")
                return
            self.session.chained_mission_id = args[0].strip()
            self._log(f"下一轮将链入任务 {self.session.chained_mission_id} 的上下文")
            return
        if name == "/missions":
            await self._list_missions()
            return
        self._log(f"未知命令: {command}（/help 查看可用命令）")

    async def _list_missions(self) -> None:
        assert self._directory is not None and self._settings is not None
        if not (self._directory / "db" / "agenthub.db").is_file():
            self._log("暂无本地任务历史（先运行一个任务）")
            return
        try:
            missions = list_recent_missions(
                state_dir=self._directory,
                workspace_root=self._workspace or self._cwd,
                model=self._settings,
                limit=20,
            )
        except (RuntimeError, OSError) as exc:
            self._log(f"error: {exc}")
            return
        if not missions:
            self._log("暂无本地任务历史")
            return
        self._log(f"{'MISSION ID':36} {'STATUS':12} OBJECTIVE")
        for mission in missions:
            mission_id = str(mission.get("id") or "")[:34]
            status = str(mission.get("status") or "")[:12]
            lines = str(mission.get("objective") or "").splitlines()
            summary = (lines[0] if lines else "")[:52]
            self._log(f"{mission_id:36} {status:12} {summary}")

    # ── mission execution ─────────────────────────────────────────────

    async def _run_mission(self, objective: str) -> None:
        assert self._settings is not None and self._directory is not None
        self.session.running = True
        self.sub_title = "running…"
        settings = self._settings
        directory = self._directory
        instructions = self._project_instructions
        chained = self.session.chained_mission_id or ""
        no_web = self._no_web_search
        timeout = self._mission_timeout
        tokens = self._max_total_tokens
        runner_timeout = self._runner_timeout_seconds
        execute = self._execute
        workspace_root = self._workspace or self._cwd

        def emit_status(status: str) -> None:
            # Called from the worker thread; marshal onto the UI thread.
            self.call_from_thread(self._log, f"  [status] {status}")

        def emit_text(text: str) -> None:
            self.call_from_thread(self._log, text)

        def emit_state(state: SessionViewState) -> None:
            from app.cli.ui import render_state_panel
            self.call_from_thread(self._log, render_state_panel(state))

        def thread_body() -> None:
            try:
                result = execute(
                    objective=objective,
                    workspace_root=workspace_root,
                    state_dir=directory,
                    model=settings,
                    max_total_tokens=tokens,
                    runner_timeout_seconds=runner_timeout,
                    mission_timeout=timeout,
                    project_instructions=instructions,
                    resume_mission_id=chained,
                    web_search=not no_web,
                    on_status=emit_status,
                    on_text=emit_text,
                    on_view_state=emit_state,
                )
            except Exception as exc:  # noqa: BLE001 - report, keep session
                self.call_from_thread(self._on_mission_error, str(exc))
                return
            self.call_from_thread(self._on_mission_done, result)

        # ``run_worker`` (synchronous in textual 8.x) starts the worker;
        # completion lands in the call_from_thread callbacks below.
        self.run_worker(
            thread_body,
            thread=True,
            exclusive=True,
            exit_on_error=False,
            name="mission",
        )

    def _on_mission_error(self, message: str) -> None:
        self.session.running = False
        assert self._settings is not None
        self.sub_title = f"{self._settings.provider}/{self._settings.model}"
        self._log(f"  error: {message}")

    def _on_mission_done(self, result: Any) -> None:
        self.session.running = False
        assert self._settings is not None
        self.sub_title = f"{self._settings.provider}/{self._settings.model}"
        self._log(
            f"  → {result.mission_id}  {result.status}  "
            f"(exit {result.exit_code}, {result.wall_seconds:.1f}s, "
            f"artifacts {len(result.artifacts)})"
        )
        if getattr(result, "workspace_files", None):
            preview = ", ".join(result.workspace_files[:6])
            more = (
                f" +{len(result.workspace_files) - 6} more"
                if len(result.workspace_files) > 6
                else ""
            )
            self._log(f"  files: {preview}{more}")
        self.session.session_missions.append(result.mission_id)
        self.session.session_records.append(
            {
                "mission_id": result.mission_id,
                "status": str(result.status),
                "wall_seconds": float(result.wall_seconds),
                "artifacts": len(result.artifacts),
                "total_tokens": int(getattr(result, "total_tokens", 0) or 0),
            }
        )
        self.session.chained_mission_id = result.mission_id

    # ── output ────────────────────────────────────────────────────────

    def _log(self, text: Any) -> None:
        self.query_one(RichLog).write(text)


def run_tui_cli(args: Any) -> int:
    """Entry from the CLI parser (see app.cli.main cmd_tui)."""
    app = AgentHubTUI(
        cwd=Path.cwd(),
        provider=args.provider,
        model=args.model,
        base_url=args.model_base_url,
        workspace=(
            Path(args.workspace).resolve() if args.workspace else None
        ),
        mission_timeout=args.mission_timeout,
        max_total_tokens=args.max_total_tokens,
        runner_timeout_seconds=args.runner_timeout_seconds,
        no_web_search=args.no_web_search,
    )
    app.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_tui_cli({}))
