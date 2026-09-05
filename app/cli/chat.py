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

import sys
import threading
import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.cli.main import _load_config

try:  # rich ships with textual; guard anyway so chat never hard-fails.
    from app.cli import ui
except Exception:  # noqa: BLE001 - degrade to plain text without rich
    ui = None  # type: ignore[assignment]

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


def _likely_side_effect_objective(objective: str) -> bool:
    """Return True only for explicit write/execute intent.

    Normal conversation must not be interrupted. Tool-level Decision remains
    the authoritative guard when the model actually requests a side effect.
    """
    text = objective.lower()
    markers = (
        "写入", "写文件", "修改", "创建文件", "删除文件", "重命名",
        "运行命令", "执行命令", "执行脚本", "安装依赖", "提交代码", "格式化",
        "write ", "edit ", "modify ", "create ", "delete ", "remove ",
        "rename ", "run ", "execute ", "install ", "format ", "commit ",
        "shell", "command",
    )
    return any(marker in text for marker in markers)
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
    "/clear         同 /new（清空上下文开始新会话）",
    "/cost          显示本会话成本摘要（任务数/产物/耗时）",
    "/diff          查看当前工作区 Git diff",
    "/changes       列出当前变更文件",
    "/patch         输出当前变更补丁",
    "/undo          撤销当前已跟踪文件变更（需确认）",
    "/status        显示当前会话设置",
    "/context       查看当前上下文与 token 使用",
    "/permissions   查看当前会话工具/路径权限",
    "/permissions export <file>  导出权限策略",
    "/permissions import <file> [merge|replace]  导入权限策略",
    "/permissions remove <allow|deny> <tool> <path>  删除权限规则",
    "/permissions check <tool> <path>  预览规则匹配",
    "/allow <tool> <path>  允许工具访问路径（本会话）",
    "/deny <tool> <path>   拒绝工具访问路径（本会话）",
    "/clear-permissions    清除本会话权限",
    "/quit          退出",
)


@dataclass
class ChatSessionState:
    """Mutable per-session state (chain + history + HITL flag)."""

    chained_mission_id: str | None = None
    session_missions: list[str] = field(default_factory=list)
    session_records: list[dict[str, Any]] = field(default_factory=list)
    compact_context: str | None = None
    always_allow: bool = False
    allowed_tools: set[str] = field(default_factory=set)
    allowed_paths: set[tuple[str, str]] = field(default_factory=set)
    denied_paths: set[tuple[str, str]] = field(default_factory=set)


def _load_permission_policy(directory: Path, session: ChatSessionState) -> None:
    path = directory / "permissions.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    session.allowed_tools.update(str(item) for item in payload.get("allowedTools", []) if str(item).strip())
    for key, target in (("allowedPaths", session.allowed_paths), ("deniedPaths", session.denied_paths)):
        for item in payload.get(key, []):
            if isinstance(item, list) and len(item) == 2:
                target.add((str(item[0]), str(item[1])))


def _save_permission_policy(directory: Path, session: ChatSessionState) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "allowedTools": sorted(session.allowed_tools),
            "allowedPaths": [list(item) for item in sorted(session.allowed_paths)],
            "deniedPaths": [list(item) for item in sorted(session.denied_paths)],
        }
        tmp = directory / "permissions.json.tmp"
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(directory / "permissions.json")
    except OSError:
        # Read-only or synthetic test workspaces keep the in-memory policy.
        return


def _permission_payload(session: ChatSessionState) -> dict[str, Any]:
    return {
        "version": 1,
        "allowedTools": sorted(session.allowed_tools),
        "allowedPaths": [list(item) for item in sorted(session.allowed_paths)],
        "deniedPaths": [list(item) for item in sorted(session.denied_paths)],
    }


def _load_permission_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    if not all(isinstance(payload.get(key, []), list) for key in ("allowedTools", "allowedPaths", "deniedPaths")):
        return None
    rules = payload.get("allowedPaths", []) + payload.get("deniedPaths", [])
    if any(not isinstance(item, list) or len(item) != 2 or not all(isinstance(value, str) for value in item) for item in rules):
        return None
    if any(not isinstance(item, str) for item in payload.get("allowedTools", [])):
        return None
    return payload


def _apply_permission_payload(session: ChatSessionState, payload: dict[str, Any], *, replace: bool) -> None:
    if replace:
        session.allowed_tools.clear()
        session.allowed_paths.clear()
        session.denied_paths.clear()
    session.allowed_tools.update(item for item in payload.get("allowedTools", []) if item.strip())
    session.allowed_paths.update((item[0], item[1]) for item in payload.get("allowedPaths", []) if item[0].strip() and item[1].strip())
    session.denied_paths.update((item[0], item[1]) for item in payload.get("deniedPaths", []) if item[0].strip() and item[1].strip())


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
    extra = ""
    if getattr(result, "cancelled", False):
        extra = " (cancelled)"
    emit(
        f"  → {result.mission_id}  {result.status}{extra}  (exit {result.exit_code}, "
        f"{result.wall_seconds:.1f}s, artifacts {len(result.artifacts)})"
    )
    tokens = int(getattr(result, "total_tokens", 0) or 0)
    if tokens:
        emit(f"  tokens: {tokens} (prompt {int(getattr(result, 'prompt_tokens', 0) or 0)} / completion {int(getattr(result, 'completion_tokens', 0) or 0)})")
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
            "mission_changed_files": list(getattr(result, "mission_changed_files", []) or []),
            "baseline_commit": getattr(result, "baseline_commit", None),
            "baseline_changed_files": list(getattr(result, "baseline_changed_files", []) or []),
            "attempt_snapshot_id": getattr(result, "attempt_snapshot_id", None),
            "prompt_tokens": int(getattr(result, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(result, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(result, "total_tokens", 0) or 0),
            "cancelled": bool(getattr(result, "cancelled", False)),
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
    read_line: Callable[[str], str] | None = None,
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
            f"\nallowed tools: {', '.join(sorted(session.allowed_tools)) or '（无）'}"
        )
        return True
    if name in ("/new", "/clear", "/unresume"):
        session.chained_mission_id = None
        if name in ("/new", "/clear"):
            session.compact_context = None
        emit(
            "已清除链式上下文，下一轮从零开始"
            if name in ("/new", "/clear")
            else "已清除链式上下文"
        )
        return True
    if name == "/cost":
        if not session.session_records:
            emit("本会话尚未运行任务（/cost 汇总本会话任务数/产物/耗时）")
            return True
        if ui is not None:
            emit("")  # spacing before the rich render
            # Reuse the shared cost renderer even in plain mode: it
            # degrades to a single muted text line.
            from rich.console import Console

            console = Console()
            console.print(ui.format_cost_line(session.session_records))
        else:
            missions = len(session.session_records)
            artifacts = sum(
                int(r.get("artifacts") or 0) for r in session.session_records
            )
            seconds = sum(
                float(r.get("wall_seconds") or 0.0)
                for r in session.session_records
            )
            emit(f"  ⌁ {missions} missions · {artifacts} artifacts · {seconds:.1f}s")
        return True
    if name == "/context":
        tokens = sum(int(r.get("total_tokens") or 0) for r in session.session_records)
        emit(
            f"session missions: {len(session.session_missions)}\n"
            f"tokens observed: {tokens:,}\n"
            f"compact context: {'active' if session.compact_context else 'inactive'}\n"
            f"resume mission: {session.chained_mission_id or '（无）'}"
        )
        return True
    if name == "/permissions":
        if args and args[0].lower() in {"export", "import"}:
            if len(args) < 2:
                emit("用法: /permissions export <file> 或 /permissions import <file> [merge|replace]")
                return True
            target = Path(args[1]).expanduser()
            if args[0].lower() == "export":
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_name(target.name + ".tmp")
                    tmp.write_text(json.dumps(_permission_payload(session), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    tmp.replace(target)
                    emit(f"已导出权限策略: {target}")
                except OSError as exc:
                    emit(f"导出失败: {exc}")
                return True
            payload = _load_permission_payload(target)
            if payload is None:
                emit("导入失败: 权限策略格式无效或文件不可读")
                return True
            mode = args[2].lower() if len(args) > 2 else "merge"
            if mode not in {"merge", "replace"}:
                emit("用法: /permissions import <file> [merge|replace]")
                return True
            _apply_permission_payload(session, payload, replace=mode == "replace")
            _save_permission_policy(directory, session)
            emit(f"已导入权限策略 ({mode})")
            return True
        if args and args[0].lower() == "remove":
            if len(args) < 4 or args[1].lower() not in {"allow", "deny"}:
                emit("用法: /permissions remove <allow|deny> <tool> <path>")
                return True
            rule = (args[2], " ".join(args[3:]))
            target = session.allowed_paths if args[1].lower() == "allow" else session.denied_paths
            if rule in target:
                target.remove(rule)
                _save_permission_policy(directory, session)
                emit(f"已删除规则: {args[1]} {rule[0]}:{rule[1]}")
            else:
                emit("未找到该权限规则")
            return True
        if args and args[0].lower() == "check":
            if len(args) < 3:
                emit("用法: /permissions check <tool> <path>")
                return True
            tool, path = args[1], " ".join(args[2:])
            denied = next((pattern for item_tool, pattern in session.denied_paths if item_tool == tool and fnmatch.fnmatch(path, pattern)), None)
            allowed = next((pattern for item_tool, pattern in session.allowed_paths if item_tool == tool and fnmatch.fnmatch(path, pattern)), None)
            if denied:
                emit(f"匹配: deny {tool}:{denied}（拒绝优先）")
            elif allowed:
                emit(f"匹配: allow {tool}:{allowed}")
            elif tool in session.allowed_tools:
                emit(f"匹配: allow-tool {tool}")
            else:
                emit("匹配: none（需要 Decision 确认）")
            return True
        emit(
            f"allowed tools: {', '.join(sorted(session.allowed_tools)) or '（无）'}\n"
            f"allowed paths: {', '.join(f'{t}:{p}' for t,p in sorted(session.allowed_paths)) or '（无）'}\n"
            f"denied paths: {', '.join(f'{t}:{p}' for t,p in sorted(session.denied_paths)) or '（无）'}"
        )
        return True
    if name in ("/allow", "/deny"):
        if len(args) < 2:
            emit(f"用法: {name} <tool> <path>")
            return True
        rule = (args[0], " ".join(args[1:]))
        (session.allowed_paths if name == "/allow" else session.denied_paths).add(rule)
        _save_permission_policy(directory, session)
        emit(f"已记录 {rule[0]}:{rule[1]}")
        return True
    if name == "/clear-permissions":
        session.allowed_tools.clear(); session.allowed_paths.clear(); session.denied_paths.clear()
        _save_permission_policy(directory, session)
        emit("已清除本会话权限")
        return True
    if name in ("/diff", "/changes", "/patch"):
        if ui is None:
            emit("当前终端不支持 Git 变更渲染")
            return True
        if name == "/changes":
            files = ui.git_changed_files(workspace_root)
            emit("  （暂无变更）" if not files else "\n".join(f"  {path}" for path in files))
            return True
        diff = ui.git_diff_text(workspace_root)
        if not diff:
            emit("  （工作区干净）")
            return True
        if name == "/patch":
            emit(diff)
        elif ui is not None:
            from rich.console import Console
            Console().print(ui.render_diff_panel(workspace_root))
        return True
    if name == "/undo":
        latest_snapshot_id = str((session.session_records[-1] if session.session_records else {}).get("attempt_snapshot_id") or "")
        if latest_snapshot_id:
            from app.cli.snapshots import load_snapshot
            snapshot = load_snapshot(workspace_root, directory / "attempt-snapshots", latest_snapshot_id)
            if snapshot is not None:
                preview_ok, preview_conflicts = snapshot.preview_restore()
                if not preview_ok:
                    emit("撤销预检失败，未修改任何文件。冲突: " + ", ".join(preview_conflicts))
                    return True
                changed = sorted(set(snapshot.baseline) | set(snapshot.post or {}))
                emit(f"撤销预览: attempt {latest_snapshot_id} 将恢复 {len(changed)} 个路径")
                answer = read_line("将恢复最近一次 attempt 的文件，继续？ [y/N] ").strip().lower() if read_line else ""
                if answer not in {"y", "yes"}:
                    emit("已取消撤销")
                    return True
                ok, conflicts = snapshot.restore()
                emit("已恢复最近一次 attempt" if ok else f"撤销被阻止，存在冲突: {', '.join(conflicts)}")
                return True
        latest = session.session_records[-1] if session.session_records else {}
        tracked = list(latest.get("mission_changed_files") or []) if latest else []
        if not tracked and ui is not None:
            tracked = ui.git_tracked_changed_files(workspace_root)
        if not tracked:
            emit("  （没有可撤销的已跟踪变更；未跟踪文件会保留）")
            return True
        if read_line is None:
            emit("  /undo 需要交互确认；未执行")
            return True
        try:
            answer = read_line(
                f"将撤销 {len(tracked)} 个已跟踪文件变更，继续？ [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in {"y", "yes"}:
            emit("已取消撤销")
            return True
        if ui.git_restore_paths(workspace_root, tracked):
            emit("已撤销已跟踪文件变更（未跟踪文件未删除）")
        else:
            emit("error: Git restore 执行失败")
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

    # Rich upgrade only when interactive on a TTY (tests inject
    # output_fn and keep the plain-string contract).
    use_rich = (
        ui is not None
        and output_fn is None
        and sys.stdout.isatty()
    )
    console = None
    if use_rich:
        from rich.console import Console

        console = Console()

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
    _load_permission_policy(directory, session)

    if use_rich and console is not None:
        # Claude-Code-style header: cwd + git branch + model channel.
        console.print(
            ui.render_header(cwd, settings.provider, settings.model, workspace_root)
        )
        if instruction_paths:
            console.print(
                "agents.md: "
                + ", ".join(
                    str(p.parent.name) or "/" for p in instruction_paths
                ),
                style=ui.STYLE_MUTED,
            )
        console.print(
            "输入任务目标运行；/help 查看命令；/quit 退出。",
            style=ui.STYLE_MUTED,
        )
        console.print()
    else:
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
                read_line=read_line,
            )
            if handled is True:
                continue
            if handled == "quit":
                emit("bye")
                return 0
            emit(f"未知命令: {objective}（/help 查看可用命令）")
            continue

        # Human-in-the-loop: confirm side-effect missions (TTY only;
        # headless paths exec/-p never enter this REPL).
        if use_rich and console is not None and not session.always_allow and _likely_side_effect_objective(objective):
            choice = ui.confirm_side_effect(console, read_line, objective)
            if choice == "no":
                emit("已取消该任务")
                continue
            if choice == "always":
                session.always_allow = True

        compact_context = session.compact_context
        is_side_effect_task = _likely_side_effect_objective(objective)
        # P0-4: cancel signal — either set externally or via Esc/KbdInt
        cancel_event = threading.Event()

        def _on_decision(decision: dict[str, Any]) -> bool:
            """P0-3: ask the user whether to allow this tool call."""
            if session.always_allow:
                return True
            if not (use_rich and console is not None):
                # Headless: degrade to allow (desktop profile)
                return True
            tool_name = str(decision.get("tool_name") or decision.get("toolName") or decision.get("tool") or "?")
            if tool_name in session.allowed_tools:
                return True
            path = str(decision.get("path") or decision.get("filePath") or decision.get("file_path") or "")
            if any(t == tool_name and fnmatch.fnmatch(path, pattern) for t, pattern in session.denied_paths):
                return False
            if any(t == tool_name and fnmatch.fnmatch(path, pattern) for t, pattern in session.allowed_paths):
                return True
            reason = str(decision.get("reason") or decision.get("riskSummary") or decision.get("prompt") or "")
            prompt_obj = type(
                "D", (), {"tool_name": tool_name, "reason": reason, "objective": objective}
            )()
            # Reuse the side-effect panel for tool-call decisions too.
            choice = ui.confirm_side_effect(
                console,
                read_line,
                f"工具: {tool_name}\n原因: {reason}",
            )
            if choice == "always":
                session.allowed_tools.add(tool_name)
                return True
            return choice == "yes"

        status_cb: Callable[[str], None]
        runner_ctx: Any = None
        if use_rich and console is not None:
            runner_ctx = ui.MissionRunner(
                console, f"running · {settings.provider}/{settings.model}"
            )
            runner_ctx.__enter__()
            status_cb = runner_ctx.on_status
            text_cb = runner_ctx.on_text
        else:
            emit(f"… 运行任务（{settings.provider}/{settings.model}）")
            status_cb = lambda status: emit(f"  [status] {status}")  # noqa: E731
        def text_cb(text: str) -> None:
                # Preserve injected output seams used by tests/callers that
                # accept only one positional argument.
                if output_fn is None:
                    print(text, end="", flush=True)
                else:
                    emit(text)

        def event_cb(event: dict[str, Any]) -> None:
            """Render non-text SSE events as compact live progress lines."""
            kind = str(event.get("eventType") or event.get("type") or "")
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
            labels = {
                "mission.lifecycle.created": "mission created",
                "work_unit.lifecycle.leased": "work unit claimed",
                "work_unit.lifecycle.started": "work unit running",
                "harness.tool.started": "tool started",
                "tool.started": "tool started",
                "harness.tool.output": "tool output",
                "tool.output": "tool output",
                "harness.tool.completed": "tool completed",
                "tool.completed": "tool completed",
                "work_unit.checkpoint.recorded": "checkpoint",
                "artifact.lifecycle.registered": "artifact registered",
                "mission.lifecycle.verifying": "verification started",
                "work_unit.lifecycle.verified": "verification completed",
                "mission.lifecycle.succeeded": "mission completed",
            }
            label = labels.get(kind)
            if label and use_rich and console is not None:
                console.print(f"  · {label}", style=ui.STYLE_MUTED)
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
                on_status=status_cb,
                on_text=text_cb,
                on_event=event_cb,
                on_view_state=(runner_ctx.on_view_state if runner_ctx is not None else None),
                on_decision_request=_on_decision,
                cancel_event=cancel_event,
                capture_attempt_snapshot=is_side_effect_task,
                tool_permission_mode=None if is_side_effect_task else "plan",
            )
        except KeyboardInterrupt:
            # P0-4 last-ditch: execute_objective should have caught and
            # turned this into a CANCELLED result; if we're here, the
            # process aborted further up — surface and continue.
            if runner_ctx is not None:
                runner_ctx.__exit__(None, None, None)
            emit("  已取消（mission 可能还在后台运行）")
            continue
        except (RuntimeError, OSError) as exc:
            if runner_ctx is not None:
                runner_ctx.__exit__(None, None, None)
            emit(f"  error: {exc}")
            continue
        if runner_ctx is not None:
            runner_ctx.__exit__(None, None, None)

        if use_rich and console is not None:
            console.print(ui.render_result_panel(result))
            diff_panel = ui.render_diff_panel(workspace_root)
            if diff_panel is not None:
                console.print(diff_panel)
            console.print(ui.format_cost_line(session.session_records))
        else:
            _print_result_compact(result, emit)
        # P0-4: cancelled missions still record but don't chain
        if getattr(result, "cancelled", False) or str(result.status).upper() == "CANCELLED":
            emit("  mission 已取消 — 不链入下一轮")
        else:
            session.session_missions.append(result.mission_id)
            # Chain the finished mission so the next turn continues the
            # story. A compacted context is one-shot: after this turn the
            # chain (which now includes this mission) takes over again.
            session.chained_mission_id = result.mission_id
            session.compact_context = None
        _record_session_mission(session, result)
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
