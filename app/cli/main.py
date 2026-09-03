"""Command line entry point for the AgentHub developer CLI (North Star M0).

Usage::

    python -m app.cli init
    python -m app.cli run "<objective>"
    python -m app.cli exec "<objective>" --json
    python -m app.cli search "<keywords>" [--status SUCCEEDED] [--days 30]
    python -m app.cli replay <MISSION_ID>

Exit codes (stable contract for CI):

    0  mission SUCCEEDED (verifier-gated)
    1  mission FAILED
    2  mission CANCELLED
    3  wait timeout / non-terminal status
    4  infrastructure error (server boot, HTTP failure, bad config)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from app.cli.review import (
    build_review_objective,
    load_findings,
    review_exit_code,
    stage_review_workspace,
)
from app.cli.receipts import cmd_replay, cmd_search
from app.cli.facts_cli import cmd_facts
from app.cli.runtime import (
    CONFIG_FILE_NAME,
    DEFAULT_MAX_TOTAL_TOKENS,
    DEFAULT_MISSION_TIMEOUT,
    DEFAULT_RUNNER_TIMEOUT_SECONDS,
    EXIT_INFRA_ERROR,
    EXIT_OK,
    MissionRunResult,
    _load_config,
    collect_agents_md_layers,
    execute_objective,
    list_recent_missions,
    merge_project_instructions,
    resolve_model_settings,
    state_dir,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agenthub",
        description=(
            "AgentHub developer CLI — run one objective through the "
            "bounded agent loop with sandboxed tools and the verifier gate."
        ),
    )
    # P0: Top-level -p/--print: single-execution without subcommand
    parser.add_argument(
        "-p", "--print",
        dest="print_mode",
        nargs="?",
        const=True,
        default=False,
        help="Single-execution mode: run one task and exit (no REPL). "
             "Usage: agenthub -p 'your task'  or  agenthub -p with_arg",
    )

    subparsers = parser.add_subparsers(dest="command", required=False)

    init_parser = subparsers.add_parser(
        "init", help="initialize the local .agenthub state directory"
    )
    _add_model_flags(init_parser)

    for name, help_text, json_flag in (
        ("run", "run one objective interactively", False),
        ("exec", "run one objective headlessly (for CI)", True),
    ):
        run_parser = subparsers.add_parser(name, help=help_text)
        run_parser.add_argument("objective", help="what the agent should do")
        _add_model_flags(run_parser)
        run_parser.add_argument(
            "--workspace",
            default=None,
            help="workspace root for file tools (default: current directory)",
        )
        run_parser.add_argument(
            "--resume",
            default=None,
            metavar="MISSION_ID",
            help="prepend a prior mission's objective and summary as context",
        )
        run_parser.add_argument(
            "--max-total-tokens",
            type=int,
            default=DEFAULT_MAX_TOTAL_TOKENS,
            help="total token budget for the harness loop (default: %(default)s)",
        )
        run_parser.add_argument(
            "--runner-timeout-seconds",
            type=float,
            default=DEFAULT_RUNNER_TIMEOUT_SECONDS,
            help="harness timeout budget in seconds (default: %(default)s)",
        )
        run_parser.add_argument(
            "--mission-timeout",
            type=float,
            default=DEFAULT_MISSION_TIMEOUT,
            help="wall-clock wait before giving up (default: %(default)s)",
        )
        run_parser.add_argument(
            "--no-web-search",
            action="store_true",
            help="disable the public-web search tool for this run",
        )
        run_parser.add_argument(
            "--permission",
            default=None,
            choices=["suggest", "edit", "auto"],
            help="tool permission tier (I-6b): suggest=read-only, "
            "edit=read/write files (default), auto=full whitelist",
        )
        if json_flag:
            run_parser.add_argument(
                    "--json",
                    action="store_true",
                    help="emit a single JSON result document on stdout",
            )
            run_parser.add_argument(
                "--jsonl", action="store_true",
                help="emit live events as JSON Lines, followed by the result",
            )

    missions_parser = subparsers.add_parser(
        "missions", help="list missions recorded in the local state"
    )
    _add_model_flags(missions_parser)
    missions_parser.add_argument(
        "--workspace",
        default=None,
        help="workspace root (default: current directory)",
    )
    missions_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="maximum missions to list (default: %(default)s)",
    )

    search_parser = subparsers.add_parser(
        "search",
        help="search local mission history and return receipts with "
        "verifier evidence (ADR-0108 P0)",
    )
    _add_model_flags(search_parser)
    search_parser.add_argument(
        "query",
        help="keyword terms to match against mission title/objective",
    )
    search_parser.add_argument(
        "--workspace",
        default=None,
        help="workspace root (default: current directory)",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="maximum receipts to return (default: %(default)s)",
    )
    search_parser.add_argument(
        "--status",
        default=None,
        help="filter on exact mission status (e.g. SUCCEEDED, FAILED)",
    )
    search_parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="only missions updated within the trailing N days",
    )
    search_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a single JSON result document on stdout",
    )
    search_parser.add_argument(
        "--scope",
        choices=["mission", "session", "events", "both", "all"],
        default="mission",
        help=(
            "search scope: mission receipts, session messages, event stream, "
            "mission+session (both), or all three (all) "
            "(default: %(default)s)"
        ),
    )

    replay_parser = subparsers.add_parser(
        "replay",
        help="show one mission's objective, status, evidence, and artifacts",
    )
    _add_model_flags(replay_parser)
    replay_parser.add_argument(
        "mission_id",
        help="mission identifier (see `agenthub missions` / `agenthub search`)",
    )
    replay_parser.add_argument(
        "--workspace",
        default=None,
        help="workspace root (default: current directory)",
    )
    replay_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a single JSON result document on stdout",
    )

    facts_parser = subparsers.add_parser(
        "facts",
        help="manage flat key-scoped project facts "
        "(.agenthub/memory.md, ADR-0107)",
    )
    facts_subparsers = facts_parser.add_subparsers(dest="facts_command")
    facts_subparsers.add_parser("list", help="list all facts")
    set_parser = facts_subparsers.add_parser("set", help="set or update a fact")
    set_parser.add_argument(
        "name",
        metavar="SECTION.KEY",
        help="fact address, e.g. python.interpreter",
    )
    set_parser.add_argument("value", help="fact value (same key supersedes)")
    get_parser = facts_subparsers.add_parser("get", help="read one fact")
    get_parser.add_argument("name", metavar="SECTION.KEY")
    remove_parser = facts_subparsers.add_parser("remove", help="delete a fact")
    remove_parser.add_argument("name", metavar="SECTION.KEY")

    review_parser = subparsers.add_parser(
        "review-pr",
        help="review a pull-request diff through the verifier-gated mission loop",
    )
    _add_model_flags(review_parser)
    review_parser.add_argument(
        "--diff-file",
        default=None,
        help="unified diff of the PR (default: PR_DIFF_FILE env var, e.g. from `gh pr diff`)",
    )
    review_parser.add_argument(
        "--workspace",
        default=None,
        help="workspace root for the review mission (default: current directory)",
    )
    review_parser.add_argument(
        "--max-total-tokens",
        type=int,
        default=DEFAULT_MAX_TOTAL_TOKENS,
        help="total token budget for the harness loop (default: %(default)s)",
    )
    review_parser.add_argument(
        "--runner-timeout-seconds",
        type=float,
        default=DEFAULT_RUNNER_TIMEOUT_SECONDS,
        help="harness timeout budget in seconds (default: %(default)s)",
    )
    review_parser.add_argument(
        "--mission-timeout",
        type=float,
        default=DEFAULT_MISSION_TIMEOUT,
        help="wall-clock wait before giving up (default: %(default)s)",
    )
    review_parser.add_argument(
        "--no-web-search",
        action="store_true",
        help="disable the public-web search tool for this review",
    )
    review_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a single JSON result document on stdout",
    )

    chat_parser = subparsers.add_parser(
        "chat", help="interactive session (slash commands, chained context)"
    )
    _add_model_flags(chat_parser)
    chat_parser.add_argument(
        "--workspace",
        default=None,
        help="workspace root for file tools (default: current directory)",
    )
    chat_parser.add_argument(
        "--max-total-tokens",
        type=int,
        default=DEFAULT_MAX_TOTAL_TOKENS,
        help="total token budget per mission (default: %(default)s)",
    )
    chat_parser.add_argument(
        "--runner-timeout-seconds",
        type=float,
        default=DEFAULT_RUNNER_TIMEOUT_SECONDS,
        help="harness timeout budget in seconds (default: %(default)s)",
    )
    chat_parser.add_argument(
        "--mission-timeout",
        type=float,
        default=DEFAULT_MISSION_TIMEOUT,
        help="wall-clock wait per mission (default: %(default)s)",
    )
    chat_parser.add_argument(
        "--no-web-search",
        action="store_true",
        help="disable the public-web search tool for this session",
    )

    tui_parser = subparsers.add_parser(
        "tui", help="full-screen terminal UI (north-star M2)"
    )
    _add_model_flags(tui_parser)
    tui_parser.add_argument(
        "--workspace",
        default=None,
        help="workspace root for file tools (default: current directory)",
    )
    tui_parser.add_argument(
        "--max-total-tokens",
        type=int,
        default=DEFAULT_MAX_TOTAL_TOKENS,
        help="total token budget per mission (default: %(default)s)",
    )
    tui_parser.add_argument(
        "--runner-timeout-seconds",
        type=float,
        default=DEFAULT_RUNNER_TIMEOUT_SECONDS,
        help="harness timeout budget in seconds (default: %(default)s)",
    )
    tui_parser.add_argument(
        "--mission-timeout",
        type=float,
        default=DEFAULT_MISSION_TIMEOUT,
        help="wall-clock wait per mission (default: %(default)s)",
    )
    tui_parser.add_argument(
        "--no-web-search",
        action="store_true",
        help="disable the public-web search tool for this session",
    )

    stacks_parser = subparsers.add_parser(
        "stacks", help="list installed runtime stacks and the pinned one"
    )
    stacks_parser.add_argument(
        "--data-dir",
        default=None,
        help="data directory holding stacks/ (default: local .agenthub)",
    )

    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help="download and verify a runtime stack, then pin it (M3)",
    )
    upgrade_parser.add_argument(
        "manifest_url",
        help="URL of the stack manifest (stack-manifest.json)",
    )
    upgrade_parser.add_argument(
        "--base-url",
        default="",
        help="base URL prefix for manifest file entries (default: manifest dir)",
    )
    upgrade_parser.add_argument(
        "--data-dir",
        default=None,
        help="data directory holding stacks/ (default: local .agenthub)",
    )

    subparsers.add_parser("doctor", help="diagnose local CLI and workspace readiness")
    completion_parser = subparsers.add_parser("completion", help="print shell completion script")
    completion_parser.add_argument("shell", choices=["bash", "zsh", "powershell"])

    # Internal: the frozen npm binary re-invokes itself with `_serve` to
    # boot the local mission-control subprocess (see
    # app.cli.runtime.server_command). Not part of the public surface.
    serve_parser = subparsers.add_parser("_serve", help=argparse.SUPPRESS)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, required=True)
    serve_parser.add_argument("--log-level", default="warning")
    return parser


def _add_model_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", default=None, help="adapter provider key")
    parser.add_argument("--model", default=None, help="model name")
    parser.add_argument("--model-base-url", default=None, help="provider base URL")


def cmd_init(args: argparse.Namespace, cwd: Path) -> int:
    directory = state_dir(cwd)
    (directory).mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = _load_config(cwd)
    settings = resolve_model_settings(
        provider=args.provider,
        model=args.model,
        base_url=args.model_base_url,
        config=config,
    )
    # Store non-secret model settings only; the API key stays env-only.
    config.update(
        {"provider": settings.provider, "model": settings.model}
    )
    if settings.base_url:
        config["base_url"] = settings.base_url
    else:
        config.pop("base_url", None)
    config_path = directory / CONFIG_FILE_NAME
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"initialized {config_path}")
    if settings.is_mock:
        print(
            "model channel: mock (offline demo — no real API calls; "
            "set AGENTHUB_CLI_MODEL_API_KEY and re-run init for a real provider)"
        )
    else:
        print(f"model channel: {settings.provider} / {settings.model}")
    print(f"workspace root: {cwd}")
    return EXIT_OK


def cmd_doctor(cwd: Path) -> int:
    """Run non-mutating local readiness checks for support and CI logs."""
    import shutil
    checks: list[tuple[str, bool, str]] = []
    checks.append(("python", True, sys.version.split()[0]))
    git = shutil.which("git")
    checks.append(("git", bool(git), git or "not found"))
    checks.append(("workspace", cwd.is_dir(), str(cwd)))
    state = state_dir(cwd)
    checks.append(("state directory", state.is_dir(), str(state)))
    key_present = any(os.environ.get(name, "").strip() for name in (
        "AGENTHUB_CLI_MODEL_API_KEY", "AGENTHUB_DESKTOP_MODEL_API_KEY",
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    ))
    checks.append(("model credentials", key_present, "environment variable" if key_present else "not set (mock fallback available)"))
    for name, ok, detail in checks:
        print(f"{'ok' if ok else 'missing':7} {name:20} {detail}")
    return EXIT_OK if all(ok for _, ok, _ in checks) else EXIT_INFRA_ERROR


def cmd_completion(shell: str) -> int:
    scripts = {
        "bash": "_agenthub_complete() { COMPREPLY=($(compgen -W 'init run exec chat tui missions search replay facts review-pr stacks upgrade doctor completion' -- \"${COMP_WORDS[COMP_CWORD]}\")); }; complete -F _agenthub_complete agenthub",
        "zsh": "#compdef agenthub\n_arguments '1:command:(init run exec chat tui missions search replay facts review-pr stacks upgrade doctor completion)'",
        "powershell": "Register-ArgumentCompleter -CommandName agenthub -ScriptBlock { param($wordToComplete) 'init','run','exec','chat','tui','missions','search','replay','facts','review-pr','stacks','upgrade','doctor','completion' | Where-Object { $_ -like \"$wordToComplete*\" } }",
    }
    print(scripts[shell])
    return EXIT_OK


def cmd_run(
    args: argparse.Namespace, cwd: Path, *, json_mode: bool
) -> int:
    jsonl_mode = bool(getattr(args, "jsonl", False))
    config = _load_config(cwd)
    settings = resolve_model_settings(
        provider=args.provider,
        model=args.model,
        base_url=args.model_base_url,
        config=config,
    )
    workspace_root = Path(args.workspace).resolve() if args.workspace else cwd
    directory = state_dir(cwd)
    directory.mkdir(parents=True, exist_ok=True)
    # Layered AGENTS.md project instructions: workspace root → cwd,
    # merged shallowest-first and injected into the system prompt.
    instruction_paths = collect_agents_md_layers(workspace_root, cwd)
    project_instructions = merge_project_instructions(instruction_paths)
    if args.resume and args.resume.strip():
        resume_mission_id = args.resume.strip()
    else:
        resume_mission_id = ""

    def _emit_status(status: str) -> None:
        if not json_mode and not jsonl_mode:
            print(f"  mission status: {status}")

    def _emit_event(event: dict[str, Any]) -> None:
        if jsonl_mode:
            print(json.dumps({"type": "event", "event": event}, ensure_ascii=False), flush=True)

    def _emit_view_state(state: Any) -> None:
        if jsonl_mode:
            print(json.dumps({"type": "state", "state": {
                "status": state.status,
                "assistantText": state.assistant_text,
                "eventCount": state.event_count,
                "verificationStatus": state.verification_status,
            }}, ensure_ascii=False), flush=True)

    if not json_mode and not jsonl_mode:
        print(f"objective: {args.objective}")
        print(f"workspace: {workspace_root}")
        print(f"model:     {settings.provider} / {settings.model}")
        if instruction_paths:
            print(
                "agents.md: "
                + ", ".join(str(p.parent.name) or "/" for p in instruction_paths)
            )
        if resume_mission_id:
            print(f"resume:   {resume_mission_id}")
        print("booting local mission-control (SQLite, desktop runner)...")
        started = time.monotonic()

    try:
        result = execute_objective(
            objective=args.objective,
            workspace_root=workspace_root,
            state_dir=directory,
            model=settings,
            max_total_tokens=args.max_total_tokens,
            runner_timeout_seconds=args.runner_timeout_seconds,
            mission_timeout=args.mission_timeout,
            project_instructions=project_instructions,
            resume_mission_id=resume_mission_id,
            web_search=not args.no_web_search,
            tool_permission_mode=getattr(args, "permission", None),
            on_status=_emit_status,
            on_event=_emit_event,
            on_view_state=_emit_view_state,
        )
    except (RuntimeError, OSError) as exc:
        if json_mode or jsonl_mode:
            print(
                json.dumps(
                    {"status": "INFRA_ERROR", "error": str(exc), "exitCode": EXIT_INFRA_ERROR}
                )
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_INFRA_ERROR

    if jsonl_mode:
        print(json.dumps({"type": "result", "result": result.to_json()}, ensure_ascii=False), flush=True)
    elif json_mode:
        print(json.dumps(result.to_json(), ensure_ascii=False, indent=2))
    else:
        _print_human(result, elapsed=time.monotonic() - started)
    return result.exit_code


def cmd_missions(args: argparse.Namespace, cwd: Path) -> int:
    config = _load_config(cwd)
    settings = resolve_model_settings(
        provider=args.provider,
        model=args.model,
        base_url=args.model_base_url,
        config=config,
    )
    workspace_root = Path(args.workspace).resolve() if args.workspace else cwd
    directory = state_dir(cwd)
    if not (directory / "db" / "agenthub.db").is_file():
        print("no local missions yet — run `agenthub run` first")
        return EXIT_OK
    try:
        missions = list_recent_missions(
            state_dir=directory,
            workspace_root=workspace_root,
            model=settings,
            limit=args.limit,
        )
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    if not missions:
        print("no local missions yet — run `agenthub run` first")
        return EXIT_OK
    print(f"{'MISSION ID':40} {'STATUS':12} OBJECTIVE")
    print("-" * 80)
    for mission in missions:
        mission_id = str(mission.get("id") or "")[:38]
        status = str(mission.get("status") or "")[:12]
        objective = str(mission.get("objective") or "").splitlines()
        summary = (objective[0] if objective else "")[:60]
        print(f"{mission_id:40} {status:12} {summary}")
    print()
    print("resume with: agenthub run \"<objective>\" --resume <MISSION_ID>")
    return EXIT_OK


def cmd_review_pr(args: argparse.Namespace, cwd: Path) -> int:
    json_mode = args.json
    config = _load_config(cwd)
    settings = resolve_model_settings(
        provider=args.provider,
        model=args.model,
        base_url=args.model_base_url,
        config=config,
    )
    workspace_root = Path(args.workspace).resolve() if args.workspace else cwd

    diff_file = args.diff_file or os.environ.get("PR_DIFF_FILE")
    if not diff_file:
        print(
            "error: no diff provided — pass --diff-file or set PR_DIFF_FILE "
            '(e.g. gh pr diff > pr.diff)',
            file=sys.stderr,
        )
        return EXIT_INFRA_ERROR
    diff_path = Path(diff_file).resolve()
    try:
        diff_text = diff_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"error: cannot read diff file {diff_path}: {exc}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    if not diff_text.strip():
        if json_mode:
            print(
                json.dumps(
                    {
                        "review": "SKIPPED_EMPTY_DIFF",
                        "blocking": [],
                        "warnings": [],
                        "nits": [],
                        "exitCode": EXIT_OK,
                    }
                )
            )
        else:
            print("empty diff — nothing to review")
        return EXIT_OK

    directory = state_dir(cwd)
    directory.mkdir(parents=True, exist_ok=True)
    # Stage the diff and the CLI-written verifier (the validator is
    # authored by the CLI, never by the agent — independent verifier).
    stage_review_workspace(workspace_root, diff_text)
    instruction_paths = collect_agents_md_layers(workspace_root, cwd)
    project_instructions = merge_project_instructions(instruction_paths)

    if not json_mode:
        print(f"review:   {diff_path.name} ({len(diff_text.splitlines())} diff lines)")
        print(f"workspace: {workspace_root}")
        print(f"model:     {settings.provider} / {settings.model}")
        print("booting local mission-control (SQLite, desktop runner)...")

    def _emit_status(status: str) -> None:
        if not json_mode:
            print(f"  mission status: {status}")

    try:
        result = execute_objective(
            objective=build_review_objective(),
            workspace_root=workspace_root,
            state_dir=directory,
            model=settings,
            max_total_tokens=args.max_total_tokens,
            runner_timeout_seconds=args.runner_timeout_seconds,
            mission_timeout=args.mission_timeout,
            project_instructions=project_instructions,
            resume_mission_id="",
            web_search=not args.no_web_search,
            on_status=_emit_status,
        )
    except (RuntimeError, OSError) as exc:
        if json_mode:
            print(
                json.dumps(
                    {"review": "INFRA_ERROR", "error": str(exc), "exitCode": EXIT_INFRA_ERROR}
                )
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_INFRA_ERROR

    findings = load_findings(workspace_root)
    exit_code = review_exit_code(result, findings)

    if json_mode:
        print(
            json.dumps(
                {
                    "review": result.status,
                    "missionId": result.mission_id,
                    "findings": findings,
                    "exitCode": exit_code,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print_review_human(result, findings, exit_code)
    return exit_code


def _print_review_human(
    result: MissionRunResult, findings: dict | None, exit_code: int
) -> None:
    print()
    print("=" * 60)
    print(f"review   {result.mission_id}")
    print(f"status   {result.status}  (exit code {exit_code})")
    print("=" * 60)
    if findings is None:
        print("no readable review-findings.json produced")
        return
    for severity in ("blocking", "warnings", "nits"):
        items = findings.get(severity) or []
        if not items:
            continue
        print(f"\n{severity.upper()} ({len(items)}):")
        for item in items:
            print(f"  {item.get('file', '?')} [{item.get('category', '?')}]")
            print(f"    issue:      {item.get('issue', '')}")
            print(f"    suggestion: {item.get('suggestion', '')}")
    if not (findings.get("blocking") or []):
        print("\nno blocking findings — review passes")


def _print_human(result: MissionRunResult, *, elapsed: float) -> None:
    print()
    print("=" * 60)
    print(f"mission  {result.mission_id}")
    print(f"status   {result.status}  (exit code {result.exit_code})")
    print(f"wall     {elapsed:.1f}s total, mission {result.wall_seconds:.1f}s")
    if result.work_unit_statuses:
        print(f"units    {', '.join(result.work_unit_statuses)}")
    if result.artifacts:
        kinds = sorted({str(a.get("kind")) for a in result.artifacts})
        print(f"artifacts {len(result.artifacts)} ({', '.join(kinds)}) — verified")
    else:
        print("artifacts none — the verifier could not confirm completion")
    if result.workspace_files:
        preview = ", ".join(result.workspace_files[:8])
        more = (
            f" (+{len(result.workspace_files) - 8} more)"
            if len(result.workspace_files) > 8
            else ""
        )
        print(f"files    {preview}{more}")
    print("=" * 60)


def _resolve_data_dir(flag_value: str | None, cwd: Path) -> Path:
    """Stack data directory: explicit flag wins, else .agenthub state."""
    if flag_value:
        return Path(flag_value).resolve()
    return state_dir(cwd)


def cmd_stacks(args: argparse.Namespace, cwd: Path) -> int:
    from app.cli.stack_installer import list_installed_stacks, read_pinned

    data_dir = _resolve_data_dir(args.data_dir, cwd)
    pinned = read_pinned(data_dir)
    stacks = list_installed_stacks(data_dir)
    if not stacks:
        print(f"no installed stacks under {data_dir}\\stacks")
        print("install one with: agenthub upgrade <manifest-url>")
        return EXIT_OK
    print(f"{'VERSION':28} {'COMMIT':10} FILES  DIR")
    print("-" * 76)
    for stack in stacks:
        marker = "  ← pinned" if stack.directory_name == pinned else ""
        print(
            f"{stack.version:28} {stack.commit[:10]:10} "
            f"{len(stack.files):5}  {stack.directory_name}{marker}"
        )
    print()
    print(f"pinned: {pinned or '（无）'}")
    print("rollback by re-pin: agenthub upgrade <old-manifest-url>")
    return EXIT_OK


def cmd_upgrade(args: argparse.Namespace, cwd: Path) -> int:
    from app.cli.stack_installer import (
        StackInstallerError,
        default_fetch_fn,
        install_stack,
    )

    data_dir = _resolve_data_dir(args.data_dir, cwd)
    base_url = args.base_url
    if not base_url:
        # Default: serve files from the manifest's directory.
        base_url = args.manifest_url.rsplit("/", 1)[0]

    def report(path: str, index: int, total: int) -> None:
        print(f"  [{index}/{total}] {path}")

    print(f"installing stack from {args.manifest_url}")
    print(f"target: {data_dir}\\stacks")
    try:
        manifest = install_stack(
            manifest_url=args.manifest_url,
            data_dir=data_dir,
            fetch_fn=default_fetch_fn,
            base_url=base_url,
            on_progress=report,
        )
    except StackInstallerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("the pinned stack is unchanged", file=sys.stderr)
        return EXIT_INFRA_ERROR
    print(
        f"installed {manifest.directory_name} "
        f"(version {manifest.version}, {len(manifest.files)} files, verified)"
    )
    print(f"pinned: {manifest.directory_name}")
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    """Hidden `_serve`: run mission-control in-process (frozen binaries)."""
    import uvicorn

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return EXIT_OK


def cli_main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        # noqa: BLE001 - best-effort, never block main path
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    cwd = Path.cwd()

    # 无 command → 自动引导 init / 单次执行 / 进入 chat
    if args.command is None:
        from app.cli.wizard import maybe_launch_wizard
        handled = maybe_launch_wizard(cwd)
        if handled:
            return EXIT_OK
        # 已配置好 → 构造完整的 args Namespace
        import json
        from app.cli.runtime import state_dir, CONFIG_FILE_NAME
        cfg_path = state_dir(cwd) / CONFIG_FILE_NAME
        cfg = {}
        if cfg_path.is_file():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        base_kwargs = dict(
            provider=cfg.get("provider", "deepseek"),
            model=cfg.get("model", "deepseek-chat"),
            model_base_url=cfg.get("base_url", None),
            workspace=None,
            mission_timeout=300.0,
            max_total_tokens=None,
            runner_timeout_seconds=120,
            no_web_search=False,
        )
        # P0-1: 没有 subcommand — 检查 argv 里有没有非 flag 的残留
        # argparse 会把 "agenthub write hello.py" 解析成 command=None
        # 然后位置参数留在 sys.argv 里（parser 只消费了 flags）
        prompt_text = ""
        if argv:
            # argv 是原始输入（或 None = sys.argv[1:]）
            raw = argv if argv is not None else sys.argv[1:]
            # 过滤掉已被 argparse 消费的 subcommand 名和 flags
            remaining = []
            skip_next = False
            known_flags = {"-p", "--print"}
            for i, tok in enumerate(raw):
                if tok in known_flags:
                    skip_next = True
                    continue
                if tok.startswith("-"):
                    continue
                if skip_next:
                    skip_next = False
                    continue
                remaining.append(tok)
            if remaining:
                prompt_text = " ".join(remaining).strip()
        
        if prompt_text:
            print(f"Objective: {prompt_text[:80]}")
            # 构造 print-mode args → 走 cmd_run(json_mode=False)
            run_args = argparse.Namespace(
                objective=prompt_text,
                provider=base_kwargs["provider"],
                model=base_kwargs["model"],
                model_base_url=base_kwargs["model_base_url"],
                workspace=None,
                mission_timeout=base_kwargs["mission_timeout"],
                max_total_tokens=None,
                runner_timeout_seconds=base_kwargs["runner_timeout_seconds"],
                no_web_search=False,
                json=False,
            )
            return cmd_run(run_args, cwd, json_mode=False)
        # P0-2: -p 模式（agenthub -p "task"）— 单次执行不进 REPL
        # With nargs='?' const=True default=False:
        #   print_mode=True  → -p with no arg (need prompt elsewhere)
        #   print_mode=str   → -p "hello" (prompt is the string)
        #   print_mode=False → no -p flag
        pm = getattr(args, "print_mode", False)
        if pm is not False:
            prompt_text = ""
            if isinstance(pm, str) and pm.strip():
                prompt_text = pm.strip()
            elif argv:
                # -p was used without arg, try to find prompt in remaining argv
                raw = argv if argv is not None else sys.argv[1:]
                remaining = [t for t in raw if not t.startswith("-")]
                if remaining:
                    prompt_text = " ".join(remaining).strip()
            if not prompt_text:
                print("错误: -p 需要一个 prompt（agenthub -p 'your task'）")
                return EXIT_USAGE_ERROR
            run_args = argparse.Namespace(
                objective=prompt_text,
                provider=base_kwargs["provider"],
                model=base_kwargs["model"],
                model_base_url=base_kwargs["model_base_url"],
                workspace=None,
                mission_timeout=base_kwargs["mission_timeout"],
                max_total_tokens=None,
                runner_timeout_seconds=base_kwargs["runner_timeout_seconds"],
                no_web_search=False,
                json=False,
            )
            return cmd_run(run_args, cwd, json_mode=False)
        # 纯无参数 → 进入交互式 chat
        chat_args = argparse.Namespace(**base_kwargs)
        from app.cli.chat import run_chat_cli
        return run_chat_cli(chat_args)

    if args.command == "init":
        # init 命令也支持无参数 → 交互式
        has_flags = any(v is not None and v != "" for v in [
            getattr(args, "provider", None),
            getattr(args, "model", None),
        ])
        if not has_flags and sys.stdin.isatty():
            from app.cli.wizard import _setup_interactive
            try:
                return _setup_interactive(cwd)
            except (EOFError, KeyboardInterrupt):
                print("\n已取消。可用 `agenthub init --provider deepseek --model deepseek-chat` 手动配置。")
                return EXIT_INFRA_ERROR
        return cmd_init(args, cwd)
    if args.command == "run":
        return cmd_run(args, cwd, json_mode=False)
    if args.command == "exec":
        return cmd_run(args, cwd, json_mode=True)
    if args.command == "missions":
        return cmd_missions(args, cwd)
    if args.command == "search":
        return cmd_search(args, cwd)
    if args.command == "replay":
        return cmd_replay(args, cwd)
    if args.command == "facts":
        return cmd_facts(args, cwd)
    if args.command == "review-pr":
        return cmd_review_pr(args, cwd)
    if args.command == "chat":
        from app.cli.chat import run_chat_cli

        return run_chat_cli(args)
    if args.command == "tui":
        from app.cli.tui import run_tui_cli

        return run_tui_cli(args)
    if args.command == "stacks":
        return cmd_stacks(args, cwd)
    if args.command == "upgrade":
        return cmd_upgrade(args, cwd)
    if args.command == "doctor":
        return cmd_doctor(cwd)
    if args.command == "completion":
        return cmd_completion(args.shell)
    if args.command == "_serve":
        return cmd_serve(args)
    parser.error(f"unknown command: {args.command}")
    return EXIT_INFRA_ERROR  # unreachable


def main() -> None:
    raise SystemExit(cli_main())
