"""Read-only execution and Runner availability projections."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain import Mission, WorkUnit, WorkUnitStatus
from app.repositories.runner_presence_repository import RunnerPresenceRepository


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


async def project_execution_status(
    mission: Mission, work_units: list[WorkUnit], presence: RunnerPresenceRepository,
    *, now: datetime | None = None,
) -> dict:
    observed_at = now or datetime.now(UTC)
    rows = []
    for unit in work_units:
        observation = await presence.matching_observation(
            mission.workspace_id, agent_id=unit.assigned_agent_id,
            adapter_type=unit.assigned_adapter, work_unit_kind=unit.kind, now=observed_at,
            required_capabilities=unit.required_capabilities,
        )
        active_lease = _active_lease(unit, observed_at)
        available = observation is not None or active_lease
        reason = _reason(mission, unit, work_units, active_lease, available)
        rows.append({
            "workUnitId": unit.id, "status": unit.status.value,
            "assignedAgentId": unit.assigned_agent_id, "assignedAdapter": unit.assigned_adapter,
            "reason": reason, "matchingRunnerAvailable": available,
            "availabilitySource": "lease" if active_lease else observation["source"] if observation else "none",
            "lastSeenAt": _iso(observation["last_seen_at"]) if observation else None,
            "availabilityExpiresAt": unit.lease.expires_at.isoformat() if active_lease else _iso(observation["expires_at"]) if observation else None,
        })
    return {
        "schemaVersion": 1, "missionId": mission.id, "workspaceId": mission.workspace_id,
        "missionStatus": mission.status.value, "observedAt": observed_at.isoformat(),
        "workUnits": rows,
    }


def _reason(mission, unit, work_units, active_lease, available):
    if mission.status.value in {"FAILED", "CANCELLED", "SUCCEEDED"}:
        return mission.status.value.lower()
    if unit.status == WorkUnitStatus.RUNNING:
        return "executing" if active_lease else "lease_expired"
    if unit.status == WorkUnitStatus.LEASED:
        return "leased" if active_lease else "lease_expired"
    if unit.status in {WorkUnitStatus.VERIFYING, WorkUnitStatus.SUCCEEDED, WorkUnitStatus.FAILED, WorkUnitStatus.CANCELLED}:
        return unit.status.value.lower()
    if mission.status.value == "WAITING_DECISION" or unit.status == WorkUnitStatus.WAITING:
        return "waiting_decision"
    statuses = {dependency.id: dependency.status for dependency in work_units}
    if any(statuses.get(identifier) != WorkUnitStatus.SUCCEEDED for identifier in unit.dependencies):
        return "waiting_dependencies"
    return "waiting_claim" if available else "waiting_runner"


def _active_lease(unit: WorkUnit, now: datetime) -> bool:
    return unit.status in {WorkUnitStatus.LEASED, WorkUnitStatus.RUNNING} and unit.lease is not None and now < unit.lease.expires_at <= now + timedelta(seconds=3600)
