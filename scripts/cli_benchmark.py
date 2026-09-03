"""Measure CLI responsiveness with a repeatable, JSON benchmark record."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.cli.runtime import (
    CliModelSettings,
    DEFAULT_MISSION_TIMEOUT,
    DEFAULT_RUNNER_TIMEOUT_SECONDS,
    execute_objective,
    state_dir,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("objective", nargs="?", default="Explain the repository structure")
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--model", default="v4-flash")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()
    root = Path.cwd()
    started = time.perf_counter()
    first_event: float | None = None
    first_token: float | None = None
    events = 0

    def on_event(_event: dict) -> None:
        nonlocal first_event, events
        events += 1
        if first_event is None:
            first_event = time.perf_counter() - started

    def on_text(_text: str) -> None:
        nonlocal first_token
        if first_token is None:
            first_token = time.perf_counter() - started

    model = CliModelSettings(args.provider, args.model, args.api_key or ("mock" if args.provider == "mock" else ""), args.base_url)
    try:
        result = execute_objective(
            objective=args.objective,
            workspace_root=root,
            state_dir=state_dir(root),
            model=model,
            mission_timeout=DEFAULT_MISSION_TIMEOUT,
            runner_timeout_seconds=DEFAULT_RUNNER_TIMEOUT_SECONDS,
            on_event=on_event,
            on_text=on_text,
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "INFRA_ERROR", "errorType": type(exc).__name__}))
        return 1
    print(json.dumps({
        "schemaVersion": 1,
        "provider": args.provider,
        "model": args.model,
        "status": result.status,
        "exitCode": result.exit_code,
        "events": events,
        "firstEventSeconds": first_event,
        "firstTokenSeconds": first_token,
        "wallSeconds": result.wall_seconds,
        "totalTokens": result.total_tokens,
    }, ensure_ascii=False))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
