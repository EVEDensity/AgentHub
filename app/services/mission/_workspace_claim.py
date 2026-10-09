"""Validation and resume fences shared by workspace claim orchestration."""
from datetime import datetime, timezone

from app.domain import MissionStatus, WorkUnitStatus


def validate_workspace_claim(workspace_id, kinds, lease_seconds, resume_mission_id):
    if not workspace_id.strip():
        raise ValueError("workspace_id must be non-empty")
    if not kinds or len(kinds) > 32 or len(kinds) != len(set(kinds)):
        raise ValueError("supported_work_unit_kinds must be non-empty, bounded and unique")
    if any(not isinstance(kind, str) or not kind.strip() or kind != kind.strip()
           or len(kind) > 255 for kind in kinds):
        raise ValueError("supported_work_unit_kinds is invalid")
    if not 1 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 1 and 3600")
    validate_resume_mission_id(resume_mission_id)


def validate_resume_mission_id(value):
    if value is not None and (not isinstance(value, str) or not value
                             or value != value.strip() or len(value) > 255):
        raise ValueError("resume_mission_id is invalid")


def is_owned_live_claim(mission, unit, runner_id, target):
    return (unit.mission_id == mission.id and mission.status == MissionStatus.RUNNING
            and unit.status in {WorkUnitStatus.LEASED, WorkUnitStatus.RUNNING}
            and unit.lease is not None and unit.lease.runner_id == runner_id
            and unit.lease.expires_at > datetime.now(timezone.utc)
            and (target is None or mission.id == target))


async def select_workspace_claim(repository, workspace_id, *, agent_id, adapter_type,
                                 kinds, runner_id, target, existing_lease_only=False):
    kwargs = dict(agent_id=agent_id, adapter_type=adapter_type,
                  supported_work_unit_kinds=kinds, runner_id=runner_id)
    if target is not None:
        kwargs["resume_mission_id"] = target
    if existing_lease_only:
        kwargs["existing_lease_only"] = True
    try:
        return await repository.get_workspace_bound_work_unit_for_claim(workspace_id, **kwargs)
    except TypeError as exc:
        # Keep normal legacy adapters compatible, but never drop an explicit
        # target and silently claim unrelated work.
        if existing_lease_only and target is None and "unexpected keyword argument" in str(exc):
            if "runner_id" in str(exc) or "existing_lease_only" in str(exc):
                return None  # Older adapters cannot safely reopen a lease.
        if target is not None or "runner_id" not in str(exc):
            raise
        kwargs.pop("runner_id")
        return await repository.get_workspace_bound_work_unit_for_claim(workspace_id, **kwargs)
