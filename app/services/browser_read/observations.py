"""Bounded browser observations are byte-integrity evidence, not fact checking."""

from __future__ import annotations

import hashlib
import time
from dataclasses import replace

from app.services.execution_artifacts import HarnessArtifact, validate_bundle
from app.services.tools.sandbox_executor import SandboxResult

from .profile import PLAYWRIGHT_VERSION, PROFILE_KIND, UPSTREAM_REVISION, canonical_json


def _artifact(execution, role, kind, media_type, content):
    identity = hashlib.sha256(
        canonical_json(
            [execution.mission_id, execution.work_unit_id, execution.attempt]
        )
    ).hexdigest()[:24]
    value = hashlib.sha256(content).hexdigest()
    return HarnessArtifact(
        f"browser-{identity}-{role}-{value}", kind, media_type, content
    )


def observed_result(result, attempt, execution, contract_id, contract_version, started):
    profile = attempt.backend.profile
    output = result.sandbox.stdout.encode("utf-8")
    limited = len(output) > profile.max_model_output_bytes
    text = output[: profile.max_model_output_bytes].decode("utf-8", errors="ignore")
    last = attempt.reads[-1] if attempt.reads else None
    last_ok = (
        last is not None and last["status"] == "completed" and last["result"]["success"]
    )
    cleanup = bool(attempt.reads) and all(
        item["result"] is not None and item["result"]["cleanupConfirmed"]
        for item in attempt.reads
    )
    success = all(
        (result.sandbox.success, last_ok, cleanup, not attempt.fatal, not limited)
    )
    reason = (
        ""
        if success
        else "Browser reads did not satisfy their completion, output and cleanup requirements."
    )
    report = {
        "schema": "agenthub.browser-execution-report.v1",
        "missionId": execution.mission_id,
        "workUnitId": execution.work_unit_id,
        "attempt": execution.attempt,
        "contractId": contract_id,
        "contractVersion": contract_version,
        "profile": PROFILE_KIND,
        "profileDigest": profile.digest,
        "runtimeSha256": profile.runtime_sha256,
        "playwrightVersion": PLAYWRIGHT_VERSION,
        "upstreamRevision": UPSTREAM_REVISION,
        "origins": list(profile.origins),
        "reads": attempt.reads,
        "cleanupConfirmed": cleanup,
        "timestampSource": "host-observation",
        "modelOutputTruncated": limited,
        "acceptance": "artifact-integrity-only",
    }
    artifacts = (
        _artifact(execution, "output", "test-result", "text/plain", text.encode()),
        _artifact(
            execution, "execution", "report", "application/json", canonical_json(report)
        ),
    )
    validate_bundle(artifacts)
    sandbox = SandboxResult(
        success,
        text,
        reason,
        0 if success else 1,
        int((time.monotonic() - started) * 1000),
        PROFILE_KIND,
        reason,
    )
    return replace(result, sandbox=sandbox, artifacts=artifacts)
