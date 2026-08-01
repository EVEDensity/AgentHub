from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from app.domain.models import Lease, Mission, MissionStatus, WorkUnit, WorkUnitStatus


class InvalidStateTransition(ValueError):
    def __init__(self, aggregate_type: str, current: str, target: str) -> None:
        self.aggregate_type = aggregate_type
        self.current = current
        self.target = target
        super().__init__(f"invalid {aggregate_type} transition: {current} -> {target}")


MISSION_TRANSITIONS: Mapping[MissionStatus, frozenset[MissionStatus]] = {
    MissionStatus.DRAFT: frozenset({MissionStatus.READY, MissionStatus.CANCELLED}),
    MissionStatus.READY: frozenset({MissionStatus.RUNNING, MissionStatus.CANCELLED}),
    MissionStatus.RUNNING: frozenset(
        {
            MissionStatus.VERIFYING,
            MissionStatus.WAITING_DECISION,
            MissionStatus.FAILED,
            MissionStatus.CANCELLED,
        }
    ),
    MissionStatus.WAITING_DECISION: frozenset(
        {MissionStatus.RUNNING, MissionStatus.FAILED, MissionStatus.CANCELLED}
    ),
    MissionStatus.VERIFYING: frozenset(
        {
            MissionStatus.SUCCEEDED,
            MissionStatus.FAILED,
            MissionStatus.RUNNING,
            MissionStatus.WAITING_DECISION,
            MissionStatus.CANCELLED,
        }
    ),
    MissionStatus.SUCCEEDED: frozenset(),
    MissionStatus.FAILED: frozenset(),
    MissionStatus.CANCELLED: frozenset(),
}


WORK_UNIT_TRANSITIONS: Mapping[WorkUnitStatus, frozenset[WorkUnitStatus]] = {
    WorkUnitStatus.PENDING: frozenset(
        {WorkUnitStatus.LEASED, WorkUnitStatus.CANCELLED}
    ),
    WorkUnitStatus.LEASED: frozenset(
        {
            WorkUnitStatus.RUNNING,
            WorkUnitStatus.RETRYING,
            WorkUnitStatus.FAILED,
            WorkUnitStatus.CANCELLED,
        }
    ),
    WorkUnitStatus.RUNNING: frozenset(
        {
            WorkUnitStatus.VERIFYING,
            WorkUnitStatus.WAITING,
            WorkUnitStatus.RETRYING,
            WorkUnitStatus.FAILED,
            WorkUnitStatus.CANCELLED,
        }
    ),
    WorkUnitStatus.WAITING: frozenset(
        {
            WorkUnitStatus.LEASED,
            WorkUnitStatus.FAILED,
            WorkUnitStatus.CANCELLED,
        }
    ),
    WorkUnitStatus.RETRYING: frozenset(
        {WorkUnitStatus.LEASED, WorkUnitStatus.FAILED, WorkUnitStatus.CANCELLED}
    ),
    WorkUnitStatus.VERIFYING: frozenset(
        {
            WorkUnitStatus.SUCCEEDED,
            WorkUnitStatus.RETRYING,
            WorkUnitStatus.FAILED,
            WorkUnitStatus.CANCELLED,
        }
    ),
    WorkUnitStatus.SUCCEEDED: frozenset(),
    WorkUnitStatus.FAILED: frozenset(),
    WorkUnitStatus.CANCELLED: frozenset(),
}


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def transition_mission(
    mission: Mission,
    target: MissionStatus,
    *,
    occurred_at: datetime,
) -> Mission:
    _require_aware(occurred_at, "occurred_at")
    if target not in MISSION_TRANSITIONS[mission.status]:
        raise InvalidStateTransition("mission", mission.status.value, target.value)
    if occurred_at < mission.updated_at:
        raise ValueError("transition time cannot be earlier than mission.updated_at")

    values = mission.model_dump()
    values.update(status=target, updated_at=occurred_at)
    return Mission.model_validate(values)


def transition_work_unit(
    work_unit: WorkUnit,
    target: WorkUnitStatus,
    *,
    occurred_at: datetime,
    lease: Lease | None = None,
) -> WorkUnit:
    _require_aware(occurred_at, "occurred_at")
    if target not in WORK_UNIT_TRANSITIONS[work_unit.status]:
        raise InvalidStateTransition("work_unit", work_unit.status.value, target.value)

    next_lease = work_unit.lease
    next_attempt = work_unit.attempt
    if target == WorkUnitStatus.LEASED:
        if lease is None:
            raise ValueError("transition to LEASED requires a new lease")
        if lease.expires_at <= occurred_at:
            raise ValueError("transition to LEASED requires an unexpired lease")
        next_lease = lease
        next_attempt += 1
    elif target == WorkUnitStatus.RUNNING:
        if lease is not None:
            raise ValueError("transition to RUNNING cannot replace the active lease")
        if work_unit.lease is None or work_unit.lease.expires_at <= occurred_at:
            raise ValueError("transition to RUNNING requires an unexpired active lease")
    else:
        if lease is not None:
            raise ValueError(f"transition to {target.value} does not accept a lease")
        next_lease = None

    values = work_unit.model_dump()
    values.update(status=target, attempt=next_attempt, lease=next_lease)
    return WorkUnit.model_validate(values)
