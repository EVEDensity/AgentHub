"""Measure CLI responsiveness with a repeatable, JSON benchmark record."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
    parser.add_argument("--task-file", type=Path, help="versioned JSON benchmark task file")
    parser.add_argument("--task-id", help="task id when --task-file contains a task suite")
    parser.add_argument("--check-thresholds", action="store_true", help="fail when measured metrics exceed task thresholds")
    parser.add_argument("--provider", default=os.environ.get("AGENTHUB_CLI_PROVIDER", "mock"))
    parser.add_argument("--model", default=os.environ.get("AGENTHUB_CLI_MODEL", "v4-flash"))
    parser.add_argument("--base-url", default=os.environ.get("AGENTHUB_CLI_MODEL_BASE_URL", ""))
    args = parser.parse_args()
    root = Path.cwd()
    task = {}
    if args.task_file:
        task = json.loads(args.task_file.read_text(encoding="utf-8"))
        if task.get("schemaVersion") != 1:
            print(json.dumps({"status": "INFRA_ERROR", "errorType": "InvalidBenchmarkSchema"}))
            return 1
        if isinstance(task.get("tasks"), list):
            task_id = args.task_id or ""
            task = next((item for item in task["tasks"] if isinstance(item, dict) and item.get("id") == task_id), {})
            if not task:
                print(json.dumps({"status": "INFRA_ERROR", "errorType": "BenchmarkTaskNotFound"}))
                return 1
        args.objective = task.get("objective", args.objective)
    started = time.perf_counter()
    first_event: float | None = None
    first_token: float | None = None
    first_tool_feedback: float | None = None
    events = 0
    reconnects = 0
    decisions = 0
    denied_decisions = 0

    def on_event(_event: dict) -> None:
        nonlocal first_event, first_tool_feedback, events, reconnects, decisions, denied_decisions
        events += 1
        event_type = str(_event.get("type") or _event.get("eventType") or "")
        if first_event is None:
            first_event = time.perf_counter() - started
        if event_type in {"tool.output", "harness.tool.output"} and first_tool_feedback is None:
            first_tool_feedback = time.perf_counter() - started
        if event_type in {"sse.reconnecting", "sse.lifecycle.reconnecting"}:
            reconnects += 1
        if event_type in {"decision.pending", "decision.lifecycle.requested"}:
            decisions += 1
            payload = _event.get("payload") if isinstance(_event.get("payload"), dict) else _event
            if payload.get("allow") is False or payload.get("resolution") in {"DENY", "FAIL_MISSION"}:
                denied_decisions += 1

    def on_text(_text: str) -> None:
        nonlocal first_token
        if first_token is None:
            first_token = time.perf_counter() - started

    api_key = os.environ.get("AGENTHUB_CLI_MODEL_API_KEY", "")
    if args.provider != "mock" and not api_key:
        print(json.dumps({"status": "SKIP", "reason": "AGENTHUB_CLI_MODEL_API_KEY is not set"}))
        return 0
    model = CliModelSettings(args.provider, args.model, api_key or "mock", args.base_url)
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
    record = {
        "schemaVersion": 1,
        "taskId": task.get("id"),
        "provider": args.provider,
        "model": args.model,
        "status": result.status,
        "exitCode": result.exit_code,
        "events": events,
        "firstEventSeconds": first_event,
        "firstTokenSeconds": first_token,
        "firstToolFeedbackSeconds": first_tool_feedback,
        "sseReconnects": reconnects,
        "decisions": decisions,
        "deniedDecisions": denied_decisions,
        "recoverySucceeded": reconnects == 0 or result.status in {"SUCCEEDED", "FAILED", "CANCELLED"},
        "wallSeconds": result.wall_seconds,
        "totalTokens": result.total_tokens,
    }
    thresholds = task.get("thresholds", {})
    failures = []
    for metric, limit in thresholds.items():
        value = record.get(metric)
        if value is None:
            failures.append(f"{metric}=missing")
        elif value > limit:
            failures.append(f"{metric}>{limit}")
    record["thresholds"] = thresholds
    record["thresholdFailures"] = failures
    print(json.dumps(record, ensure_ascii=False))
    return 1 if args.check_thresholds and failures else result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
