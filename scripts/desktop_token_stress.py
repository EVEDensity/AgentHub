"""Desktop local runner token stress benchmark (R4).

Runs real desktop-task Missions against a live model channel through the
in-process desktop local runner (AGENTHUB_DESKTOP_LOCAL_RUNNER=1) and
measures the token/time economics per scenario:

- S1  light single-file task (~2-4 iterations)
- S2  multi-file mini project (~4-6 iterations)
- S3  iterative self-review/fix loop (~6-8 iterations, approaches the cap)
- S4  budget truncation: tiny AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOTAL_TOKENS

Each run boots an isolated mission-control process on SQLite with its own
data directory and workspace root, creates one manual Mission through the
HTTP API, waits for a terminal Mission status and then reads:

- the ``execution_checkpoints`` table (per-event iteration / tool-call /
  cumulative prompt+completion tokens / budget + failure reasons),
- the Mission / WorkUnit / Artifact HTTP API,
- the resulting workspace file set.

Usage::

    set AGENTHUB_DESKTOP_MODEL_API_KEY=<key>   # never written anywhere
    python scripts/desktop_token_stress.py --scenarios s1 s2 s3 s4 --runs 2

The API key is read from the environment only; it is never written to any
file, log or output JSON produced by this script.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
KEY_ENV = "AGENTHUB_DESKTOP_MODEL_API_KEY"
WORKSPACE_ID = "local-admin"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_MODEL_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_PROVIDER = "openai"

TERMINAL_MISSION_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}

_S1_OBJECTIVE = (
    "创建 hello.py，文件里实现打印 hello world 的逻辑"
    "（print(\"hello world\") 即可）。完成后用一句话总结。"
)

SCENARIOS: dict[str, dict[str, Any]] = {
    "s1": {
        "title": "S1 轻量单文件",
        "objective": _S1_OBJECTIVE,
        "max_total_tokens": 200_000,
    },
    "s2": {
        "title": "S2 多文件小项目",
        "objective": (
            "创建一个 utils 小项目：math_tools.py（提供 add/sub/mul/div 四个函数）、"
            "str_tools.py（提供 slugify 和 truncate 两个函数）、io_tools.py"
            "（提供 read_text 和 write_text 两个函数），每个模块带简短 docstring；"
            "再创建 __init__.py 导出这三个模块；最后创建 README.md 说明项目结构与用法。"
            "完成后用一句话总结。"
        ),
        "max_total_tokens": 200_000,
    },
    "s3": {
        "title": "S3 迭代修复（逼近迭代上限）",
        "objective": (
            "创建 calc.py，实现 add/subtract/multiply/divide 四则运算函数。"
            "然后开始自检：用 file_read 通读你上一步写的 calc.py，"
            "如果你认为代码里有 bug 就用 file_edit 修复，修复后再次通读检查；"
            "重复这个检查-修复循环，直到你确信代码没有任何 bug 才允许停止，"
            "每次检查都要真的调用工具去读文件，最后用一句话总结。"
        ),
        "max_total_tokens": 200_000,
    },
    "s4": {
        "title": "S4 预算截断验证（total_tokens=3000）",
        "objective": _S1_OBJECTIVE,
        "max_total_tokens": 3_000,
    },
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Desktop local runner token stress benchmark (R4).",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=sorted(SCENARIOS),
        default=sorted(SCENARIOS),
        help="Scenarios to run (default: all).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=2,
        help="Runs per scenario (default: 2).",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL", DEFAULT_MODEL),
        help="Model name sent to the provider (default: %(default)s).",
    )
    parser.add_argument(
        "--model-base-url",
        default=os.environ.get(
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL_BASE_URL", DEFAULT_MODEL_BASE_URL
        ),
        help="OpenAI-compatible base URL of the provider.",
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help="Adapter provider key (default: %(default)s).",
    )
    parser.add_argument(
        "--runner-timeout-seconds",
        type=float,
        default=300.0,
        help="Harness timeout budget in seconds (default: %(default)s).",
    )
    parser.add_argument(
        "--mission-timeout",
        type=float,
        default=420.0,
        help="Wall-clock wait per mission before giving up (default: %(default)s).",
    )
    parser.add_argument(
        "--port-base",
        type=int,
        default=28_100,
        help="First port used for the per-run mission-control processes.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Path of the JSON result file (default: <data-root>/results-<ts>.json).",
    )
    parser.add_argument(
        "--data-root",
        default=None,
        help="Directory for per-run data (default: %%TEMP%%/agenthub-r4-stress).",
    )
    return parser.parse_args(argv)


def _free_port(start: int) -> int:
    port = start
    for _ in range(100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                port += 1
                continue
            return port
    raise RuntimeError("no free port found")


def build_contract(contract_id: str, time_seconds: int) -> dict[str, Any]:
    """Desktop-style manual contract with a deterministic artifact-set policy.

    The ``artifact-set.v1`` criterion lets the runner's unattended verification
    loop close SUCCEEDED missions on its own; the published desktop-task
    Artifact kind is ``test-result``.
    """
    return {
        "id": contract_id,
        "version": 1,
        "repositoryScopes": [],
        "allowedCapabilities": [],
        "budgets": {"timeSeconds": time_seconds, "modelCost": 1, "retries": 0},
        "acceptanceCriteria": [
            {
                "id": "desktop-artifacts",
                "kind": "manual",
                "description": "桌面本地任务产物核验（文件存在、非空、字节一致）。",
                "required": True,
                "configuration": {
                    "evaluator": "artifact-set.v1",
                    "workUnitKinds": ["desktop.task"],
                    "minimumArtifacts": 1,
                    "requiredArtifactKinds": ["test-result"],
                },
            }
        ],
        "decisionGates": [],
        "forbiddenActions": [],
    }


def server_env(
    run_dir: Path,
    args: argparse.Namespace,
    *,
    max_total_tokens: int,
    port: int,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "AGENTHUB_DB_BACKEND": "sqlite",
            "AGENTHUB_SQLITE_PATH": str(run_dir / "db" / "agenthub.db"),
            "AGENTHUB_LOCAL_DATA": str(run_dir / "data"),
            "AGENTHUB_DESKTOP_LOCAL_RUNNER": "1",
            # The runner logs into mission-control (this very process) over
            # HTTP, so it must target the actual per-run port.
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_BASE_URL": f"http://127.0.0.1:{port}",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY": "1",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY_INTERVAL_SECONDS": "1",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_DERIVATION_INTERVAL_SECONDS": "1",
            "AGENTHUB_DESKTOP_WORKSPACE_ROOT": str(run_dir / "workspace"),
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL": args.model,
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL_BASE_URL": args.model_base_url,
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_PROVIDER": args.provider,
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_ITERATIONS": "8",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOOL_CALLS": "32",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOTAL_TOKENS": str(max_total_tokens),
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_TIMEOUT_SECONDS": str(
                args.runner_timeout_seconds
            ),
            # Keep the self-hosted adapter path even if a gateway is configured
            # in the ambient environment.
            "AGENTHUB_LLM_GATEWAY": "",
        }
    )
    return env


def wait_for_server(base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(base_url + "/", timeout=3)
            if response.status_code == 200:
                time.sleep(3.0)  # let the post-startup runner task log in
                return
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    raise RuntimeError(f"mission-control did not become ready at {base_url}")


def login(client: httpx.Client) -> str:
    response = client.post(
        "/api/auth/login", json={"name": "admin", "password": "admin123"}
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("accessToken")
    if not isinstance(token, str) or not token:
        raise RuntimeError("login returned no access token")
    return token


def count_workspace_files(workspace_root: Path) -> list[str]:
    files: list[str] = []
    if not workspace_root.exists():
        return files
    for path in sorted(workspace_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace_root).as_posix()
        if relative.startswith(".git/") or relative == ".git":
            continue
        files.append(relative)
    return files


def read_checkpoints(db_path: Path, mission_id: str) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for _ in range(10):
        try:
            connection = sqlite3.connect(str(db_path))
            try:
                cursor = connection.execute(
                    "SELECT sequence, phase, iteration, tool_calls, prompt_tokens, "
                    "completion_tokens, model_cost, terminal, failure_reason, created_at "
                    "FROM execution_checkpoints WHERE mission_id = ? ORDER BY sequence",
                    (mission_id,),
                )
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
            finally:
                connection.close()
        except sqlite3.Error as exc:  # table not created yet / brief lock
            last_error = exc
            time.sleep(1.0)
    raise RuntimeError(f"cannot read execution_checkpoints: {last_error}")


def classify_budget(rows: list[dict[str, Any]]) -> str | None:
    terminal_reason = ""
    for row in rows:
        if row["terminal"]:
            terminal_reason = (row["failure_reason"] or "").lower()
    if "total-token budget" in terminal_reason:
        return "total_tokens"
    if "iteration budget" in terminal_reason:
        return "iterations"
    if "tool-call budget" in terminal_reason:
        return "tool_calls"
    if "timed out" in terminal_reason:
        return "timeout"
    if "model execution failed" in terminal_reason:
        return "provider_error"
    if terminal_reason:
        return "other"
    return None


def summarize_run(
    *,
    scenario: str,
    run_index: int,
    mission: dict[str, Any],
    work_units: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
    workspace_files: list[str],
    wall_seconds: float,
    server_seconds: float,
    max_total_tokens: int,
    timed_out_waiting: bool,
) -> dict[str, Any]:
    model_rows = [
        row for row in checkpoints if row["phase"] == "harness.model.completed"
    ]
    per_round: list[dict[str, int]] = []
    previous_prompt = 0
    previous_completion = 0
    for row in model_rows:
        per_round.append(
            {
                "iteration": row["iteration"],
                "prompt_tokens": row["prompt_tokens"] - previous_prompt,
                "completion_tokens": row["completion_tokens"] - previous_completion,
                "cumulative_prompt_tokens": row["prompt_tokens"],
                "cumulative_completion_tokens": row["completion_tokens"],
            }
        )
        previous_prompt = row["prompt_tokens"]
        previous_completion = row["completion_tokens"]

    terminal_row = next((row for row in checkpoints if row["terminal"]), None)
    totals = model_rows[-1] if model_rows else None

    return {
        "scenario": scenario,
        "run": run_index,
        "mission_id": mission.get("id"),
        "mission_status": mission.get("status"),
        "work_unit_statuses": [unit.get("status") for unit in work_units],
        "artifact_count": len(artifacts),
        "artifact_kinds": sorted({str(a.get("kind")) for a in artifacts}),
        "wall_seconds": round(wall_seconds, 2),
        "server_seconds": round(server_seconds, 2),
        "iterations": max((row["iteration"] for row in checkpoints), default=0),
        "tool_calls": max((row["tool_calls"] for row in checkpoints), default=0),
        "model_calls": len(model_rows),
        "prompt_tokens_total": totals["prompt_tokens"] if totals else 0,
        "completion_tokens_total": totals["completion_tokens"] if totals else 0,
        "total_tokens": (
            totals["prompt_tokens"] + totals["completion_tokens"] if totals else 0
        ),
        "per_round_tokens": per_round,
        "terminal_phase": terminal_row["phase"] if terminal_row else None,
        "failure_reason": (
            terminal_row["failure_reason"] if terminal_row and terminal_row["terminal"]
            else None
        ),
        "budget_hit": classify_budget(checkpoints),
        "max_total_tokens_budget": max_total_tokens,
        "workspace_files": workspace_files,
        "workspace_file_count": len(workspace_files),
        "waiting_timeout": timed_out_waiting,
    }


def run_one(
    scenario: str,
    run_index: int,
    args: argparse.Namespace,
    data_root: Path,
    port: int,
) -> dict[str, Any]:
    config = SCENARIOS[scenario]
    run_dir = data_root / f"{scenario}-r{run_index}-{uuid.uuid4().hex[:8]}"
    (run_dir / "db").mkdir(parents=True, exist_ok=True)
    (run_dir / "data").mkdir(parents=True, exist_ok=True)
    workspace_root = run_dir / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)

    max_total_tokens = int(config["max_total_tokens"])
    env = server_env(run_dir, args, max_total_tokens=max_total_tokens, port=port)
    base_url = f"http://127.0.0.1:{port}"
    server_log = (run_dir / "server.log").open("wb")
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    server_started = time.monotonic()
    try:
        wait_for_server(base_url, timeout=120)
        server_seconds = time.monotonic() - server_started

        with httpx.Client(base_url=base_url, timeout=30) as client:
            token = login(client)
            headers = {"Authorization": f"Bearer {token}"}
            mission_id = f"mis-{scenario}-r{run_index}-{uuid.uuid4().hex[:8]}"
            contract = build_contract(
                f"contract-{scenario}-r{run_index}-{uuid.uuid4().hex[:8]}",
                time_seconds=int(args.runner_timeout_seconds),
            )
            created = time.monotonic()
            response = client.post(
                "/api/v1/missions",
                headers=headers,
                json={
                    "id": mission_id,
                    "title": f"{config['title']} run{run_index}",
                    "objective": config["objective"],
                    "workspaceId": WORKSPACE_ID,
                    "source": {"type": "manual"},
                    "contract": contract,
                },
            )
            response.raise_for_status()
            mission = response.json()
            response = client.post(
                f"/api/v1/missions/{mission_id}/start", headers=headers
            )
            response.raise_for_status()
            mission = response.json()

            status_history: list[str] = [str(mission.get("status"))]
            timed_out_waiting = False
            deadline = time.monotonic() + args.mission_timeout
            while mission.get("status") not in TERMINAL_MISSION_STATUSES:
                if time.monotonic() > deadline:
                    timed_out_waiting = True
                    break
                time.sleep(2.0)
                response = client.get(
                    f"/api/v1/missions/{mission_id}", headers=headers
                )
                response.raise_for_status()
                mission = response.json()
                if status_history[-1] != mission.get("status"):
                    status_history.append(str(mission.get("status")))
            wall_seconds = time.monotonic() - created

            units_response = client.get(
                f"/api/v1/missions/{mission_id}/work-units", headers=headers
            )
            units_response.raise_for_status()
            work_units = units_response.json().get("workUnits", [])
            artifacts_response = client.get(
                f"/api/v1/missions/{mission_id}/artifacts", headers=headers
            )
            artifacts_response.raise_for_status()
            artifacts = artifacts_response.json().get("artifacts", [])

        checkpoints = read_checkpoints(
            run_dir / "db" / "agenthub.db", str(mission.get("id"))
        )
        return summarize_run(
            scenario=scenario,
            run_index=run_index,
            mission=mission,
            work_units=work_units,
            artifacts=artifacts,
            checkpoints=checkpoints,
            workspace_files=count_workspace_files(workspace_root),
            wall_seconds=wall_seconds,
            server_seconds=server_seconds,
            max_total_tokens=max_total_tokens,
            timed_out_waiting=timed_out_waiting,
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        server_log.close()


def scenario_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    def medians(field: str) -> float:
        values = [record[field] for record in records]
        return round(statistics.median(values), 2) if values else 0.0

    return {
        "runs": len(records),
        "statuses": [record["mission_status"] for record in records],
        "median_wall_seconds": medians("wall_seconds"),
        "median_total_tokens": medians("total_tokens"),
        "median_prompt_tokens": medians("prompt_tokens_total"),
        "median_completion_tokens": medians("completion_tokens_total"),
        "median_iterations": medians("iterations"),
        "median_tool_calls": medians("tool_calls"),
        "median_workspace_file_count": medians("workspace_file_count"),
        "budget_hits": sorted(
            {str(record["budget_hit"]) for record in records if record["budget_hit"]}
        ),
    }


def print_table(records: list[dict[str, Any]]) -> None:
    header = (
        f"{'scenario':9} {'run':>3} {'status':>9} {'wall_s':>7} {'iter':>4} "
        f"{'tools':>5} {'model':>5} {'prompt':>7} {'compl':>6} {'total':>7} "
        f"{'files':>5} {'budget':>12}"
    )
    print(header)
    print("-" * len(header))
    for record in records:
        print(
            f"{record['scenario']:9} {record['run']:>3} "
            f"{str(record['mission_status']):>9} {record['wall_seconds']:>7} "
            f"{record['iterations']:>4} {record['tool_calls']:>5} "
            f"{record['model_calls']:>5} {record['prompt_tokens_total']:>7} "
            f"{record['completion_tokens_total']:>6} "
            f"{record['total_tokens']:>7} {record['workspace_file_count']:>5} "
            f"{str(record['budget_hit'] or '-'):>12}"
        )


def main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    args = parse_args(argv)
    if not os.environ.get(KEY_ENV):
        print(
            f"error: {KEY_ENV} is not set; the stress benchmark only reads the "
            "model API key from the environment.",
            file=sys.stderr,
        )
        return 2

    data_root = Path(
        args.data_root
        or (Path(os.environ.get("TEMP", tempfile.gettempdir())) / "agenthub-r4-stress")
    )
    data_root.mkdir(parents=True, exist_ok=True)

    runs: list[dict[str, Any]] = []
    port = _free_port(args.port_base)
    for scenario in args.scenarios:
        for run_index in range(1, args.runs + 1):
            port = _free_port(port + 1)
            print(
                f"[{scenario} run{run_index}] starting mission-control on :{port}",
                flush=True,
            )
            record = run_one(scenario, run_index, args, data_root, port)
            runs.append(record)
            print(
                f"[{scenario} run{run_index}] status={record['mission_status']} "
                f"wall={record['wall_seconds']}s iter={record['iterations']} "
                f"tools={record['tool_calls']} tokens={record['total_tokens']} "
                f"budget={record['budget_hit'] or '-'}",
                flush=True,
            )

    summary = {
        scenario: scenario_summary([r for r in runs if r["scenario"] == scenario])
        for scenario in args.scenarios
    }
    output = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": args.model,
            "model_base_url": args.model_base_url,
            "provider": args.provider,
            "runner_timeout_seconds": args.runner_timeout_seconds,
            "runner_max_iterations": 8,
            "runner_max_tool_calls": 32,
            "runs_per_scenario": args.runs,
            "platform": sys.platform,
        },
        "summary": summary,
        "runs": runs,
    }

    out_path = Path(args.out) if args.out else data_root / (
        f"results-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print_table(runs)
    print(f"\nresults written to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
