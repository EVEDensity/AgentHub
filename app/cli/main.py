"""Command line entry point for the AgentHub developer CLI (North Star M0).

Usage::

    python -m app.cli init
    python -m app.cli run "<objective>"
    python -m app.cli exec "<objective>" --json

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
import sys
import time
from pathlib import Path
from typing import Any

from app.cli.runtime import (
    DEFAULT_MAX_TOTAL_TOKENS,
    DEFAULT_MISSION_TIMEOUT,
    DEFAULT_RUNNER_TIMEOUT_SECONDS,
    CliModelSettings,
    EXIT_INFRA_ERROR,
    EXIT_OK,
    MissionRunResult,
    collect_agents_md_layers,
    execute_objective,
    list_recent_missions,
    merge_project_instructions,
    resolve_model_settings,
    state_dir,
)

CONFIG_FILE_NAME = "config.json"


def _load_config(cwd: Path) -> dict[str, Any]:
    config_path = state_dir(cwd) / CONFIG_FILE_NAME
    if not config_path.is_file():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agenthub",
        description=(
            "AgentHub developer CLI — run one objective through the "
            "bounded agent loop with sandboxed tools and the verifier gate."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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
        if json_flag:
            run_parser.add_argument(
                "--json",
                action="store_true",
                help="emit a single JSON result document on stdout",
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


def cmd_run(
    args: argparse.Namespace, cwd: Path, *, json_mode: bool
) -> int:
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
        if not json_mode:
            print(f"  mission status: {status}")

    if not json_mode:
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
            on_status=_emit_status,
        )
    except (RuntimeError, OSError) as exc:
        if json_mode:
            print(
                json.dumps(
                    {"status": "INFRA_ERROR", "error": str(exc), "exitCode": EXIT_INFRA_ERROR}
                )
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_INFRA_ERROR

    if json_mode:
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


def cli_main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    cwd = Path.cwd()
    if args.command == "init":
        return cmd_init(args, cwd)
    if args.command == "run":
        return cmd_run(args, cwd, json_mode=False)
    if args.command == "exec":
        return cmd_run(args, cwd, json_mode=True)
    if args.command == "missions":
        return cmd_missions(args, cwd)
    parser.error(f"unknown command: {args.command}")
    return EXIT_INFRA_ERROR  # unreachable


def main() -> None:
    raise SystemExit(cli_main())
