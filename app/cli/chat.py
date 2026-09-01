"""Interactive chat REPL for the developer CLI (north-star M2).

``python -m app.cli chat`` opens a session-bound loop over the same
engine the one-shot commands use (isolated SQLite mission-control +
desktop runner + verifier gate). Each turn runs one Mission; the
previous turn's Mission id is chained as resume context so the
conversation accumulates honestly — prior objectives, statuses, and
deposited summaries, never invented history.

Slash commands (typed instead of an objective):

    /help          list commands
    /missions      list recorded missions
    /resume <id>   chain a specific prior mission into the next turn
    /unresume      clear the chained context
    /compact       fold the session chain into one compact context document
    /replay        replay every mission of this session (objective/status/summary)
    /new           start a fresh conversation (clears the chain)
    /status        show current session settings
    /quit          exit

Full-screen TUI (ratatui/ink) remains a later M2 deliverable; this REPL
is the terminal-interactive baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.cli.main import _load_config
from app.cli.runtime import (
    DEFAULT_MAX_TOTAL_TOKENS,
    DEFAULT_MISSION_TIMEOUT,
    DEFAULT_RUNNER_TIMEOUT_SECONDS,
    CliModelSettings,
    MissionControlClient,
    MissionControlProcess,
    build_compact_context,
    collect_agents_md_layers,
    execute_objective,
    list_recent_missions,
    merge_project_instructions,
    resolve_model_settings,
    state_dir,
)

_PROMPT = "agenthub> "
_BANNER_LINES = (
    "AgentHub interactive session — engine: desktop runner + verifier gate",
    "Type an objective to run one mission; /help for commands; /quit to exit.",
)

_HELP_LINES = (
    "/help          显示本帮助",
    "/missions      列出本地历史任务",
    "/resume <id>   将指定任务链入下一轮上下文",
    "/unresume      清除链式上下文",
    "/compact       将会话任务链压缩为一份上下文文档（下一轮注入摘要而非全链）",
    "/replay        回放本会话每个任务（目标/状态/耗时/产物）",
    "/new           开始全新会话（清除链式上下文）",
    "/status        显示当前会话设置",
    "/quit          退出",
)


@dataclass
class ChatSessionState:
    """Mutable per-session state (chain + history)."""

    chained_mission_id: str | None = None
    session_missions: list[str] = field(default_factory=list)
    session_records: list[dict[str, Any]] = field(default_factory=list)
    compact_context: str | None = None


def _print_missions(missions: list[dict[str, Any]], emit: Callable[..., None]) -> None:
    if not missions:
        emit("  （暂无历史任务）")
        return
    emit(f"  {'MISSION ID':36} {'STATUS':12} OBJECTIVE")
    for mission in missions:
        mission_id = str(mission.get("id") or "")[:34]
        status = str(mission.get("status") or "")[:12]
        lines = str(mission.get("objective") or "").splitlines()
        summary = (lines[0] if lines else "")[:52]
        emit(f"  {mission_id:36} {status:12} {summary}")


def _print_result_compact(result: Any, emit: Callable[..., None]) -> None:
    emit(
        f"  → {result.mission_id}  {result.status}  (exit {result.exit_code}, "
        f"{result.wall_seconds:.1f}s, artifacts {len(result.artifacts)})"
    )
    if result.workspace_files:
        preview = ", ".join(result.workspace_files[:6])
        more = (
            f" +{len(result.workspace_files) - 6} more"
            if len(result.workspace_files) > 6
            else ""
        )
        emit(f"  files: {preview}{more}")


def _record_session_mission(session: ChatSessionState, result: Any) -> None:
    """Keep a replayable digest of this turn's mission (I-6c /replay)."""
    session.session_records.append(
        {
            "mission_id": result.mission_id,
            "objective_first_line": (
                (result.objective or "").strip().splitlines() or [""]  # type: ignore[attr-defined]
            )[0][:120],
            "status": result.status,
            "wall_seconds": result.wall_seconds,
            "artifacts": len(result.artifacts),
            "workspace_files": list(result.workspace_files),
        }
    )


def _compact_session_context(
    *,
    settings: CliModelSettings,
    workspace_root: Path,
    directory: Path,
    session: ChatSessionState,
    emit: Callable[..., None],
) -> None:
    """Fold the session chain into one compact context document."""
    if not session.session_missions:
        emit("本会话尚无任务，无需压缩（先运行一个任务）")
        return
    try:
        with MissionControlProcess(
            state_dir=directory,
            workspace_root=workspace_root,
            model=settings,
        ) as process:
            with MissionControlClient(process.base_url) as client:
                client.login()
                document = build_compact_context(
                    client, list(session.session_missions)
                )
    except (RuntimeError, OSError) as exc:
        emit(f"error: {exc}")
        return
    if not document.strip():
        emit("压缩结果为空（本地任务记录不可读），链式上下文保持不变")
        return
    session.compact_context = document
    emit(
        f"已压缩 {len(session.session_missions)} 个任务为一份上下文文档"
        f"（{len(document)} 字符）；下一轮将注入该摘要而非全链。"
        "继续运行新任务后链会自动恢复逐轮链入。"
    )


def _replay_session(session: ChatSessionState, emit: Callable[..., None]) -> None:
    """Replay every mission of this session from recorded digests."""
    if not session.session_records:
        emit("本会话尚无任务（回放只覆盖本会话内运行过的任务）")
        return
    emit(f"本会话共 {len(session.session_records)} 个任务：")
    for index, record in enumerate(session.session_records, start=1):
        emit(
            f"  {index}. {record['mission_id']}  {record['status']}  "
            f"{record['wall_seconds']:.1f}s  artifacts {record['artifacts']}"
        )
        emit(f"     目标: {record['objective_first_line'] or '（空）'}")
        if record["workspace_files"]:
            preview = ", ".join(record["workspace_files"][:6])
            more = (
                f" +{len(record['workspace_files']) - 6} more"
                if len(record["workspace_files"]) > 6
                else ""
            )
            emit(f"     文件: {preview}{more}")
    if session.compact_context:
        emit("（当前处于 /compact 压缩上下文模式）")


def _run_slash_command(
    command: str,
    *,
    settings: CliModelSettings,
    workspace_root: Path,
    directory: Path,
    session: ChatSessionState,
    emit: Callable[..., None],
) -> bool | str:
    """Handle one slash command. True=continue, 'quit'=exit, False=unknown."""
    parts = command.split()
    name = parts[0].lower()
    args = parts[1:]

    if name in ("/quit", "/exit", "/q"):
        return "quit"
    if name == "/help":
        for line in _HELP_LINES:
            emit(line)
        return True
    if name == "/status":
        emit(
            f"model: {settings.provider} / {settings.model}\n"
            f"workspace: {workspace_root}\n"
            f"state: {directory}\n"
            f"chained mission: {session.chained_mission_id or '（无）'}\n"
            f"session missions: {len(session.session_missions)}"
        )
        return True
    if name in ("/new", "/unresume"):
        session.chained_mission_id = None
        if name == "/new":
            session.compact_context = None
        emit(
            "已清除链式上下文，下一轮从零开始"
            if name == "/new"
            else "已清除链式上下文"
        )
        return True
    if name == "/compact":
        _compact_session_context(
            settings=settings,
            workspace_root=workspace_root,
            directory=directory,
            session=session,
            emit=emit,
        )
        return True
    if name == "/replay":
        _replay_session(session, emit)
        return True
    if name == "/resume":
        if not args:
            emit("用法: /resume <mission_id>")
            return True
        session.chained_mission_id = args[0].strip()
        emit(f"下一轮将链入任务 {session.chained_mission_id} 的上下文")
        return True
    if name == "/missions":
        if not (directory / "db" / "agenthub.db").is_file():
            emit("暂无本地任务历史（先运行一个任务）")
            return True
        try:
            missions = list_recent_missions(
                state_dir=directory,
                workspace_root=workspace_root,
                model=settings,
                limit=20,
            )
        except (RuntimeError, OSError) as exc:
            emit(f"error: {exc}")
            return True
        _print_missions(missions, emit)
        return True
    return False


def chat_session(
    *,
    cwd: Path,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    workspace: Path | None,
    mission_timeout: float = DEFAULT_MISSION_TIMEOUT,
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS,
    runner_timeout_seconds: float = DEFAULT_RUNNER_TIMEOUT_SECONDS,
    no_web_search: bool = False,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[..., None] | None = None,
) -> int:
    """Run the interactive session. Returns a CLI exit code.

    ``input_fn``/``output_fn`` exist for tests; production uses the
    built-in input()/print().
    """
    read_line = input_fn or input
    emit = output_fn or print

    config = _load_config(cwd)
    settings = resolve_model_settings(
        provider=provider, model=model, base_url=base_url, config=config
    )
    workspace_root = workspace or cwd
    directory = state_dir(cwd)
    directory.mkdir(parents=True, exist_ok=True)
    instruction_paths = collect_agents_md_layers(workspace_root, cwd)
    project_instructions = merge_project_instructions(instruction_paths)
    session = ChatSessionState()

    for line in _BANNER_LINES:
        emit(line)
    emit(
        f"model: {settings.provider} / {settings.model}   "
        f"workspace: {workspace_root}"
    )
    if instruction_paths:
        emit(
            "agents.md: "
            + ", ".join(str(p.parent.name) or "/" for p in instruction_paths)
        )
    emit()

    while True:
        try:
            raw = read_line(_PROMPT)
        except (EOFError, KeyboardInterrupt):
            emit()
            emit("bye")
            return 0
        objective = (raw or "").strip()
        if not objective:
            continue

        if objective.startswith("/"):
            handled = _run_slash_command(
                objective,
                settings=settings,
                workspace_root=workspace_root,
                directory=directory,
                session=session,
                emit=emit,
            )
            if handled is True:
                continue
            if handled == "quit":
                emit("bye")
                return 0
            emit(f"未知命令: {objective}（/help 查看可用命令）")
            continue

        emit(f"… 运行任务（{settings.provider}/{settings.model}）")
        compact_context = session.compact_context
        try:
            result = execute_objective(
                objective=objective,
                workspace_root=workspace_root,
                state_dir=directory,
                model=settings,
                max_total_tokens=max_total_tokens,
                runner_timeout_seconds=runner_timeout_seconds,
                mission_timeout=mission_timeout,
                project_instructions=project_instructions,
                resume_mission_id=session.chained_mission_id or "",
                web_search=not no_web_search,
                context_text=compact_context or "",
                on_status=lambda status: emit(f"  [status] {status}"),
            )
        except (RuntimeError, OSError) as exc:
            emit(f"  error: {exc}")
            continue
        _print_result_compact(result, emit)
        session.session_missions.append(result.mission_id)
        _record_session_mission(session, result)
        # Chain the finished mission so the next turn continues the
        # story. A compacted context is one-shot: after this turn the
        # chain (which now includes this mission) takes over again.
        session.chained_mission_id = result.mission_id
        session.compact_context = None
        emit()


def run_chat_cli(args: Any) -> int:
    """Entry from the CLI parser (see app.cli.main cmd_chat)."""
    return chat_session(
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_chat_cli({}))
