"""Read-only CLI preflight for Runner-owned, complete checkpoint recovery.

Public metadata is a fence, never a replacement for the private Harness image.
The production claimed-work resolver restores that image and reconciles receipts.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.services.workspace_fingerprint import workspace_revision


def execution_resume_target(mission_id: str, context_text: str) -> str | None:
    return mission_id if mission_id and not context_text.strip() else None


@dataclass(frozen=True)
class ResumeExecutionPlan:
    """Metadata preflight; ``resume_input`` remains empty by design."""

    mission_id: str
    work_unit_id: str
    attempt: int
    lease_id: str | None
    checkpoint: dict[str, Any] | None
    pending_decision: dict[str, Any] | None
    receipt_decision: str
    can_resume: bool
    refusal_reason: str | None = None
    resume_input: Any | None = None
    execution_context: dict[str, Any] | None = None
    observation_only: bool = False


def _field(row: Mapping[str, Any], camel: str, snake: str) -> Any:
    return row.get(camel, row.get(snake))


def _positive_int(value: Any) -> int:
    return value if type(value) is int and value > 0 else 0


def _v2_anchor_error(checkpoint: Mapping[str, Any], mission_id: str) -> str | None:
    if _field(checkpoint, "resumeProtocolVersion", "resume_protocol_version") != 2:
        return "legacy checkpoint has no complete strict resume image"
    if _field(checkpoint, "missionId", "mission_id") != mission_id:
        return "checkpoint belongs to a different mission"
    if not checkpoint.get("id") or not _field(checkpoint, "workUnitId", "work_unit_id"):
        return "checkpoint execution identity is missing"
    if not _positive_int(checkpoint.get("attempt")) or not _positive_int(checkpoint.get("sequence")):
        return "checkpoint attempt or sequence is invalid"
    iteration = checkpoint.get("iteration")
    if type(iteration) is not int or iteration < 0:
        return "checkpoint iteration must be a non-negative integer"
    if checkpoint.get("terminal") is not False:
        return "terminal checkpoint cannot resume execution"
    action = _field(checkpoint, "nextAction", "next_action")
    if not isinstance(action, Mapping) or not _digest(action.get("resumeImageDigest")):
        return "checkpoint has no valid private image digest"
    fingerprints = (
        _field(checkpoint, "workspaceRevision", "workspace_revision"),
        _field(checkpoint, "contextManifestDigest", "context_manifest_digest"),
    )
    if not all(_digest(value) for value in fingerprints):
        return "checkpoint execution fingerprints are invalid"
    return None


def _digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", value) is not None


def resume_work_unit(
    client: Any,
    mission_id: str,
    workspace_root: Path,
    *,
    strict: bool = False,
    expected_context_manifest_digest: str | None = None,
    receipt_store: Any | None = None,
    revision_reader: Callable[[Path], str] = workspace_revision,
) -> dict[str, Any]:
    """Read authenticated metadata without leasing, executing or restoring tools.

    Lenient callers can inspect old checkpoints. V2 preflight intentionally does
    not judge workspace/receipt reconciliation: a completed side effect may have
    changed the workspace after the last public anchor.
    """
    mission = client.get_mission(mission_id)
    if not isinstance(mission, dict) or mission.get("id") != mission_id:
        raise RuntimeError(f"cannot safely resume mission {mission_id}: invalid mission payload")
    units = client.work_units(mission_id)
    checkpoints = client.checkpoints(mission_id)
    if not isinstance(units, list) or not isinstance(checkpoints, list):
        raise TypeError("invalid resume metadata collection")
    latest = max(
        (row for row in checkpoints if isinstance(row, dict)),
        key=lambda row: _positive_int(row.get("sequence")),
        default=None,
    )
    current_revision = revision_reader(workspace_root)
    anchor = latest or {}
    recorded_revision = _field(anchor, "workspaceRevision", "workspace_revision")
    recorded_context = _field(anchor, "contextManifestDigest", "context_manifest_digest")
    error = "no durable checkpoint" if latest is None else _v2_anchor_error(anchor, mission_id)
    if strict and error:
        raise RuntimeError(f"cannot safely resume mission {mission_id}: {error}")
    context_matches = expected_context_manifest_digest in {None, recorded_context}
    if strict and not context_matches:
        raise RuntimeError(f"cannot safely resume mission {mission_id}: context manifest changed since checkpoint")
    receipt_decision = _receipt_diagnostic(anchor, receipt_store)
    return {
        "missionId": mission_id,
        "missionStatus": mission.get("status"),
        "workUnits": units,
        "checkpoint": latest,
        "workspaceRevision": current_revision,
        "recordedWorkspaceRevision": recorded_revision,
        "revisionMatches": recorded_revision == current_revision,
        "recordedContextManifestDigest": recorded_context,
        "contextManifestMatches": context_matches,
        "legacy": _field(anchor, "resumeProtocolVersion", "resume_protocol_version") != 2,
        "nextAction": _field(anchor, "nextAction", "next_action"),
        "idempotencyKey": _field(anchor, "idempotencyKey", "idempotency_key"),
        "receiptDecision": receipt_decision,
    }


def _receipt_diagnostic(anchor: Mapping[str, Any], store: Any) -> str:
    key = _field(anchor, "idempotencyKey", "idempotency_key")
    replay = getattr(store, "replay_decision", None)
    if key and callable(replay):
        return str(replay(str(key), strict=True))
    return "not_checked"


def _lease_error(unit: Mapping[str, Any], attempt: int, runner_id: str) -> str | None:
    if unit.get("status") not in {"RUNNING", "LEASED"}:
        return "checkpoint requires an active leased execution"
    if _positive_int(unit.get("attempt")) != attempt:
        return "checkpoint belongs to a different execution attempt"
    lease = unit.get("lease")
    if not isinstance(lease, Mapping) or not lease.get("id"):
        return "running work unit has no lease"
    if _field(lease, "runnerId", "runner_id") != runner_id:
        return "execution lease belongs to another runner"
    if _positive_int(lease.get("attempt")) != attempt:
        return "execution lease belongs to a different attempt"
    value = _field(lease, "expiresAt", "expires_at")
    try:
        expiry = datetime.fromisoformat(value)
        if expiry.tzinfo is None or expiry <= datetime.now(UTC):
            return "execution lease is expired or has no timezone"
    except (AttributeError, TypeError, ValueError):
        return "execution lease expiry is invalid"
    return None


def _anchor_identity(anchor: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(anchor.get(field) for field in (
        "id", "missionId", "workUnitId", "attempt", "sequence", "phase", "iteration",
        "terminal", "stateDigest", "resumeProtocolVersion", "nextAction",
        "idempotencyKey", "workspaceRevision", "contextManifestDigest",
    ))


def _execution_projection(
    client: Any, mission_id: str, unit_id: str, lease_id: str,
    checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    payload = client.execution_context(mission_id, unit_id, lease_id=lease_id)
    projected = payload.get("executionContext") if isinstance(payload, dict) else None
    anchor = projected.get("checkpoint") if isinstance(projected, dict) else None
    if not isinstance(anchor, dict) or _anchor_identity(anchor) != _anchor_identity(checkpoint):
        raise RuntimeError("execution checkpoint changed or is missing in lease-fenced projection")
    return payload


def _related_decision(
    item: Mapping[str, Any], unit_id: str, attempt: int, checkpoint: Mapping[str, Any],
) -> bool:
    if _field(item, "workUnitId", "work_unit_id") != unit_id:
        return False
    if _positive_int(item.get("attempt", item.get("executionAttempt"))) != attempt:
        return False
    action = _field(checkpoint, "nextAction", "next_action") or {}
    for expected, actual in (
        (_field(action, "callId", "call_id"), item.get("callId", item.get("toolCallId", item.get("tool_call_id")))),
        (_field(checkpoint, "idempotencyKey", "idempotency_key"), _field(item, "idempotencyKey", "idempotency_key")),
    ):
        if expected and actual and expected != actual:
            return False
    return True


def _pending_decision(client: Any, mission_id: str, unit_id: str, attempt: int,
                      checkpoint: Mapping[str, Any]) -> dict[str, Any] | None:
    related = [d for d in client.decisions(mission_id) if isinstance(d, dict)
               and d.get("status", d.get("decisionStatus", "PENDING")) == "PENDING"
               and _related_decision(d, unit_id, attempt, checkpoint)]
    if len(related) > 1:
        raise RuntimeError("multiple pending decisions for work unit")
    return related[0] if related else None


def _heartbeat_existing(client: Any, mission_id: str, unit: dict[str, Any],
                        attempt: int, runner_id: str, lease_seconds: int) -> None:
    lease_id = unit["lease"]["id"]
    renewed = client.heartbeat_work_unit(mission_id, unit["id"], lease_id=lease_id,
                                        lease_seconds=lease_seconds)
    lease = renewed.get("lease") if isinstance(renewed, dict) else None
    if not isinstance(lease, dict) or lease.get("id") != lease_id:
        raise RuntimeError("execution lease identity changed during heartbeat")
    error = _lease_error({**unit, "lease": lease}, attempt, runner_id)
    if error:
        raise RuntimeError(error)


def prepare_resume_execution(
    client: Any,
    mission_id: str,
    workspace_root: Path,
    *,
    runner_id: str = "local-admin",
    lease_seconds: int = 300,
    expected_context_manifest_digest: str | None = None,
    receipt_store: Any | None = None,
    gate_reader: Callable[..., dict[str, Any]] = resume_work_unit,
) -> ResumeExecutionPlan:
    """Preflight an existing live same-owner attempt; never acquire another one.

    An expired lease is a refusal, not permission to recover/increment attempts.
    Only Runner's private image restoration can produce HarnessResumeInput.
    """
    gate = gate_reader(
        client, mission_id, workspace_root, strict=True,
        expected_context_manifest_digest=expected_context_manifest_digest,
        receipt_store=receipt_store,
    )
    checkpoint = gate.get("checkpoint")
    anchor = checkpoint if isinstance(checkpoint, dict) else {}
    unit_id = str(_field(anchor, "workUnitId", "work_unit_id") or "")
    attempt = _positive_int(anchor.get("attempt"))
    receipt_decision = str(gate.get("receiptDecision", "not_checked"))

    def plan(reason: str | None, *, lease_id: str | None = None,
             decision: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> ResumeExecutionPlan:
        return ResumeExecutionPlan(mission_id, unit_id, attempt, lease_id, checkpoint,
                                   decision, receipt_decision, reason is None, reason,
                                   execution_context=context)

    error = "no durable checkpoint" if not anchor else _v2_anchor_error(anchor, mission_id)
    if error:
        return plan(error)
    units = gate.get("workUnits", [])
    unit = next((u for u in units if isinstance(u, dict) and u.get("id") == unit_id), None)
    if unit is None:
        return plan("checkpoint work unit is not part of mission")
    error = _lease_error(unit, attempt, runner_id)
    if error:
        return plan(error)
    lease_id = unit["lease"]["id"]
    try:
        execution_context = _execution_projection(client, mission_id, unit_id, lease_id, anchor)
        decision = _pending_decision(client, mission_id, unit_id, attempt, anchor)
        # Authenticated heartbeat is the final lease check. Preserve its exact
        # owner, lease and attempt; do not accept a newly acquired identity.
        _heartbeat_existing(client, mission_id, unit, attempt, runner_id, lease_seconds)
    except (httpx.HTTPError, RuntimeError, KeyError, TypeError, ValueError) as exc:
        return plan(f"execution preflight failed: {exc}")
    return plan(None, lease_id=lease_id, decision=decision,
                context=execution_context)


def _observation_plan(gate: Mapping[str, Any], mission_id: str) -> ResumeExecutionPlan | None:
    """Observe completed execution while independent verification owns success."""
    if gate.get("missionId") != mission_id:
        return None
    checkpoint = gate.get("checkpoint")
    anchor = checkpoint if isinstance(checkpoint, dict) else {}
    unit_id = str(_field(anchor, "workUnitId", "work_unit_id") or "")
    attempt = _positive_int(anchor.get("attempt"))
    terminal = gate.get("missionStatus") in {"SUCCEEDED", "FAILED", "CANCELLED"}
    if not terminal:
        if not unit_id or not attempt:
            return None
        if _field(anchor, "missionId", "mission_id") != mission_id:
            return None
        if anchor.get("terminal") is not True or anchor.get("phase") != "harness.execution.completed":
            return None
        units = gate.get("workUnits", [])
        unit = next((u for u in units if isinstance(u, dict) and u.get("id") == unit_id), None)
        if not unit or unit.get("status") != "VERIFYING" or _positive_int(unit.get("attempt")) != attempt:
            return None
    return ResumeExecutionPlan(mission_id, unit_id, attempt, None, checkpoint, None,
                               "not_checked", True, observation_only=True)


def prepare_resume_handoff(
    client: Any, mission_id: str, workspace_root: Path, *,
    execution_preparer: Callable[..., ResumeExecutionPlan] = prepare_resume_execution,
    gate_reader: Callable[..., dict[str, Any]] = resume_work_unit,
) -> ResumeExecutionPlan:
    """Handle one completion race without treating observation as execution.

    Workers may finish before CLI preflight. A terminal Mission is reported from
    durable state; a VERIFYING WorkUnit is only observed until the verifier acts.
    An active attempt still needs the original strict execution preflight.
    """
    mission = client.get_mission(mission_id)
    if not isinstance(mission, dict) or mission.get("id") != mission_id:
        raise RuntimeError("resume mission payload does not match requested mission")
    observed = _observation_plan({"missionId": mission_id, "missionStatus": mission.get("status")}, mission_id)
    if observed:
        return observed
    try:
        plan = execution_preparer(client, mission_id, workspace_root)
    except (httpx.HTTPError, RuntimeError, KeyError, TypeError, ValueError):
        observed = _observation_plan(gate_reader(client, mission_id, workspace_root), mission_id)
        if observed:
            return observed
        raise
    if plan.can_resume:
        return plan
    observed = _observation_plan(gate_reader(client, mission_id, workspace_root), mission_id)
    return observed or plan
