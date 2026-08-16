"""Run an isolated PostgreSQL-to-supervisor Decision expiry smoke test."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import asyncpg

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT / "deploy" / "docker-compose.decision-expiry-smoke.yml"
MISSION_ID = "mis-decision-expiry-smoke"
CONTRACT_ID = "contract-decision-expiry-smoke"
WORK_UNIT_ID = "wu-decision-expiry-smoke"
DECISION_ID = "dec-decision-expiry-smoke"


@dataclass(frozen=True, slots=True)
class SmokeRuntime:
    project_name: str
    compose_environment: dict[str, str]
    env_file: Path

    @property
    def compose_command(self) -> list[str]:
        return [
            "docker",
            "compose",
            "--env-file",
            str(self.env_file),
            "-p",
            self.project_name,
            "-f",
            str(COMPOSE_FILE),
        ]


def _run(
    command: list[str],
    *,
    environment: dict[str, str],
    timeout: int,
) -> str:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        output = "\n".join(
            part.strip()
            for part in (completed.stdout, completed.stderr)
            if part.strip()
        )
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: "
            f"{' '.join(command[:3])}\n{output}"
        )
    return completed.stdout.strip()


def _published_port(output: str) -> int:
    line = output.strip().splitlines()[-1] if output.strip() else ""
    try:
        host, raw_port = line.rsplit(":", 1)
        port = int(raw_port)
    except (ValueError, IndexError) as exc:
        raise ValueError("Docker Compose returned an invalid published port") from exc
    if host not in {"127.0.0.1", "0.0.0.0", "[::]", "::"}:
        raise ValueError("smoke service was not bound to a loopback-compatible host")
    if not 1 <= port <= 65535:
        raise ValueError("Docker Compose returned an invalid published port")
    return port


async def _seed_expired_decision(database_url: str) -> None:
    connection = await asyncpg.connect(database_url)
    requested_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    try:
        async with connection.transaction():
            await connection.execute(
                """INSERT INTO mission_contracts(id, version, document)
                   VALUES($1, 1, $2::jsonb)""",
                CONTRACT_ID,
                json.dumps(
                    {
                        "id": CONTRACT_ID,
                        "version": 1,
                        "repositoryScopes": [],
                        "allowedCapabilities": [],
                        "budgets": {
                            "timeSeconds": 60,
                            "modelCost": 0,
                            "retries": 0,
                        },
                        "acceptanceCriteria": [
                            {
                                "id": "manual-review",
                                "kind": "manual",
                                "description": "Smoke criterion",
                                "required": True,
                                "configuration": {},
                            }
                        ],
                        "decisionGates": [],
                        "forbiddenActions": [],
                    }
                ),
            )
            await connection.execute(
                """INSERT INTO missions(
                       id, workspace_id, title, objective, source, contract_id,
                       status, plan_version, created_by, created_at, updated_at
                   ) VALUES(
                       $1, $2, $3, $4, $5::jsonb, $6, 'WAITING_DECISION', 1,
                       $7::jsonb, $8, $8
                   )""",
                MISSION_ID,
                "workspace-decision-expiry-smoke",
                "Decision expiry smoke",
                "Verify fail-closed expiry supervision.",
                json.dumps({"type": "manual"}),
                CONTRACT_ID,
                json.dumps({"type": "human", "id": "smoke-operator"}),
                requested_at,
            )
            await connection.execute(
                """INSERT INTO work_units(
                       id, mission_id, kind, dependencies, input_refs,
                       expected_outputs, required_capabilities, assigned_adapter,
                       status, attempt, lease
                   ) VALUES(
                       $1, $2, 'smoke', '[]'::jsonb, '[]'::jsonb,
                       $3::jsonb, '[]'::jsonb, NULL, 'VERIFYING', 1, NULL
                   )""",
                WORK_UNIT_ID,
                MISSION_ID,
                json.dumps([{"kind": "report", "required": True}]),
            )
            await connection.execute(
                """INSERT INTO decisions(
                       id, mission_id, work_unit_id, attempt, context_digest,
                       reason_code, criterion_ids, options, recommended_option,
                       risk_summary, status, version, requested_by, requested_at,
                       expires_at, resolution, rationale, resolved_by, resolved_at
                   ) VALUES(
                       $1, $2, $3, 1, $4, 'no_applicable_policy', $5::jsonb,
                       $6::jsonb, 'FAIL_MISSION', $7, 'PENDING', 1, $8::jsonb,
                       $9, $10, NULL, NULL, NULL, NULL
                   )""",
                DECISION_ID,
                MISSION_ID,
                WORK_UNIT_ID,
                "sha256:" + "c" * 64,
                json.dumps(["manual-review"]),
                json.dumps(["RETRY_WORK_UNIT", "FAIL_MISSION"]),
                "Smoke verification cannot prove the criterion.",
                json.dumps({"type": "service", "id": "mission-control"}),
                requested_at,
                expires_at,
            )
    finally:
        await connection.close()


async def _assert_expiry_closure(database_url: str) -> None:
    connection = await asyncpg.connect(database_url)
    try:
        decision = await connection.fetchrow(
            """SELECT status, version, resolution, rationale,
                      resolved_by->>'id' AS resolved_by_id,
                      resolved_at, expires_at
               FROM decisions WHERE id=$1""",
            DECISION_ID,
        )
        work_unit = await connection.fetchrow(
            "SELECT status, lease FROM work_units WHERE id=$1",
            WORK_UNIT_ID,
        )
        mission = await connection.fetchrow(
            "SELECT status, updated_at FROM missions WHERE id=$1",
            MISSION_ID,
        )
        events = await connection.fetch(
            """SELECT aggregate_type, aggregate_id, sequence, event_type,
                      correlation_id, causation_id, actor->>'id' AS actor_id
               FROM mission_events
               WHERE correlation_id=$1
               ORDER BY occurred_at, aggregate_type""",
            MISSION_ID,
        )
        evidence_count = await connection.fetchval(
            "SELECT COUNT(*) FROM evidence WHERE mission_id=$1",
            MISSION_ID,
        )

        if decision is None or work_unit is None or mission is None:
            raise AssertionError("supervisor did not preserve all smoke aggregates")
        if (
            decision["status"] != "EXPIRED"
            or decision["version"] != 2
            or decision["resolution"] is not None
            or decision["rationale"] != "Decision expired before human resolution."
            or decision["resolved_by_id"] != "mission-control"
            or decision["resolved_at"] < decision["expires_at"]
        ):
            raise AssertionError("Decision did not close with fail-closed expiry")
        if work_unit["status"] != "FAILED" or work_unit["lease"] is not None:
            raise AssertionError("WorkUnit did not fail without a retained lease")
        if mission["status"] != "FAILED":
            raise AssertionError("Mission did not fail after Decision expiry")
        if evidence_count != 0:
            raise AssertionError("Decision expiry must not create Evidence")

        by_type = {event["aggregate_type"]: event for event in events}
        expected_event_types = {
            "decision": "decision.lifecycle.expired",
            "work_unit": "work_unit.lifecycle.decision_expired",
            "mission": "mission.lifecycle.decision_expired",
        }
        if len(events) != 3 or set(by_type) != set(expected_event_types):
            raise AssertionError("expiry must append exactly three aggregate events")
        for aggregate_type, event_type in expected_event_types.items():
            event = by_type[aggregate_type]
            if (
                event["event_type"] != event_type
                or event["sequence"] != 1
                or event["correlation_id"] != MISSION_ID
                or event["actor_id"] != "mission-control"
            ):
                raise AssertionError(f"invalid {aggregate_type} expiry event")
        if by_type["decision"]["causation_id"] is not None:
            raise AssertionError("Decision expiry event must start the causal chain")
        decision_event_id = await connection.fetchval(
            """SELECT event_id FROM mission_events
               WHERE aggregate_type='decision' AND aggregate_id=$1""",
            DECISION_ID,
        )
        work_unit_event_id = await connection.fetchval(
            """SELECT event_id FROM mission_events
               WHERE aggregate_type='work_unit' AND aggregate_id=$1""",
            WORK_UNIT_ID,
        )
        if by_type["work_unit"]["causation_id"] != decision_event_id:
            raise AssertionError("WorkUnit event is not caused by Decision expiry")
        if by_type["mission"]["causation_id"] != work_unit_event_id:
            raise AssertionError("Mission event is not caused by WorkUnit failure")
    finally:
        await connection.close()


async def _event_count(database_url: str) -> int:
    connection = await asyncpg.connect(database_url)
    try:
        return int(
            await connection.fetchval(
                "SELECT COUNT(*) FROM mission_events WHERE correlation_id=$1",
                MISSION_ID,
            )
        )
    finally:
        await connection.close()


def _read_readiness(port: int) -> dict[str, Any]:
    with urlopen(f"http://127.0.0.1:{port}/readyz", timeout=5) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise TypeError("readiness response must be an object")
    return payload


def _assert_sanitized_readiness(payload: dict[str, Any]) -> None:
    worker = payload.get("worker")
    if payload.get("status") != "ready" or not isinstance(worker, dict):
        raise AssertionError("supervisor did not report ready worker state")
    if worker.get("expired") != 1 or worker.get("failedPolls") != 0:
        raise AssertionError("supervisor counters do not reflect one clean expiry")
    rendered = json.dumps(payload, sort_keys=True)
    for forbidden in (MISSION_ID, WORK_UNIT_ID, DECISION_ID, "postgresql://"):
        if forbidden in rendered:
            raise AssertionError("readiness response exposed sensitive state")


def run_smoke(*, timeout_seconds: int) -> None:
    password = secrets.token_hex(24)
    project_name = f"agenthub-expiry-smoke-{os.getpid()}"
    with tempfile.TemporaryDirectory(prefix="agenthub-expiry-smoke-") as directory:
        temporary_root = Path(directory).resolve()
        secret_file = temporary_root / "database-url.secret"
        env_file = temporary_root / "compose.env"
        container_database_url = (
            f"postgresql://agenthub:{password}@postgres:5432/agenthub"
        )
        secret_file.write_text(container_database_url, encoding="utf-8")
        env_file.write_text(
            "\n".join(
                (
                    f"AGENTHUB_SMOKE_POSTGRES_PASSWORD={password}",
                    f"AGENTHUB_SMOKE_DATABASE_URL_FILE={secret_file.as_posix()}",
                )
            ),
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment.pop("DATABASE_URL", None)
        runtime = SmokeRuntime(project_name, environment, env_file)
        print(f"Decision expiry smoke project: {project_name}")

        try:
            _run(
                [*runtime.compose_command, "up", "-d", "--wait", "postgres"],
                environment=runtime.compose_environment,
                timeout=timeout_seconds,
            )
            postgres_port = _published_port(
                _run(
                    [*runtime.compose_command, "port", "postgres", "5432"],
                    environment=runtime.compose_environment,
                    timeout=30,
                )
            )
            host_database_url = (
                f"postgresql://agenthub:{password}@127.0.0.1:"
                f"{postgres_port}/agenthub"
            )
            migration_environment = dict(runtime.compose_environment)
            migration_environment["DATABASE_URL"] = host_database_url
            _run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                environment=migration_environment,
                timeout=timeout_seconds,
            )
            asyncio.run(_seed_expired_decision(host_database_url))

            _run(
                [
                    *runtime.compose_command,
                    "up",
                    "-d",
                    "--build",
                    "--wait",
                    "--wait-timeout",
                    str(timeout_seconds),
                    "decision-expiry-service",
                ],
                environment=runtime.compose_environment,
                timeout=timeout_seconds,
            )
            service_port = _published_port(
                _run(
                    [
                        *runtime.compose_command,
                        "port",
                        "decision-expiry-service",
                        "8099",
                    ],
                    environment=runtime.compose_environment,
                    timeout=30,
                )
            )
            asyncio.run(_assert_expiry_closure(host_database_url))
            _assert_sanitized_readiness(_read_readiness(service_port))
            first_event_count = asyncio.run(_event_count(host_database_url))
            time.sleep(1)
            second_event_count = asyncio.run(_event_count(host_database_url))
            if first_event_count != 3 or second_event_count != 3:
                raise AssertionError("idle supervisor duplicated expiry events")
        finally:
            active_error = sys.exc_info()[0] is not None
            try:
                _run(
                    [
                        *runtime.compose_command,
                        "down",
                        "--volumes",
                        "--remove-orphans",
                        "--timeout",
                        "10",
                    ],
                    environment=runtime.compose_environment,
                    timeout=60,
                )
            except Exception as cleanup_error:
                if not active_error:
                    raise
                print(
                    "warning: smoke cleanup failed: "
                    f"project={project_name} type={type(cleanup_error).__name__}"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=int, default=240)
    arguments = parser.parse_args()
    if arguments.timeout_seconds < 30:
        parser.error("--timeout-seconds must be at least 30")
    run_smoke(timeout_seconds=arguments.timeout_seconds)
    print("Decision expiry smoke passed.")


if __name__ == "__main__":
    main()
