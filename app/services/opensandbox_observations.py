"""Byte artifacts for command observations; no tests or code-correctness claim."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace

from app.services.execution_artifacts import HarnessArtifact, validate_bundle
from app.services.harness_types import HarnessResult
from app.services.opensandbox_protocol import WORKLOAD_IMAGE
from app.services.tools.sandbox_executor import SandboxResult


def _artifact(
    execution, role: str, kind: str, media_type: str, content: bytes
) -> HarnessArtifact:
    scope = json.dumps(
        [execution.mission_id, execution.work_unit_id, execution.attempt],
        separators=(",", ":"),
    )
    identity = hashlib.sha256(scope.encode()).hexdigest()[:24]
    digest = hashlib.sha256(content).hexdigest()
    return HarnessArtifact(
        f"sandbox-{identity}-{role}-{digest}", kind, media_type, content
    )


def observed_result(
    result: HarnessResult, attempt, execution, job_id: str, started: float
) -> HarnessResult:
    text = attempt.sanitize(result.sandbox.stdout)
    raw = text.encode()
    model_output_limited = len(raw) > attempt.limits.max_output_bytes
    text = raw[: attempt.limits.max_output_bytes].decode("utf-8", errors="ignore")
    last = attempt.commands[-1] if attempt.commands else None
    command_ok = (
        last is not None and last["status"] == "completed" and last["exitCode"] == 0
    )
    success = all(
        (
            result.sandbox.success,
            command_ok,
            attempt.cleanup_confirmed,
            not attempt.fatal,
            not model_output_limited,
        )
    )
    reason = (
        ""
        if success
        else "Sandbox command execution did not satisfy its completion and cleanup requirements."
    )
    report = {
        "schema": "agenthub.opensandbox-execution-report.v1",
        "missionId": execution.mission_id,
        "workUnitId": execution.work_unit_id,
        "attempt": execution.attempt,
        "backendKind": "opensandbox",
        "image": WORKLOAD_IMAGE,
        "sandboxId": attempt.sandbox_id,
        "jobId": job_id,
        "commands": attempt.commands,
        "cleanupConfirmed": attempt.cleanup_confirmed,
        "timestampSource": "host-observation",
        "modelOutputTruncated": model_output_limited,
        "acceptance": "artifact-integrity-only",
    }
    artifacts = (
        _artifact(execution, "output", "test-result", "text/plain", text.encode()),
        _artifact(
            execution,
            "execution",
            "report",
            "application/json",
            json.dumps(
                report, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode(),
        ),
    )
    validate_bundle(artifacts)
    sandbox = SandboxResult(
        success,
        text,
        reason,
        0 if success else 1,
        int((time.monotonic() - started) * 1000),
        "opensandbox-shell.v1",
        reason,
    )
    return replace(result, sandbox=sandbox, artifacts=artifacts)
