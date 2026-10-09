"""Exact source/kind/adapter policy shared by claim and context admission."""

from app.domain import Mission, MissionSourceType, WorkUnit


def is_desktop_task_root(mission: Mission, unit: WorkUnit) -> bool:
    if unit.parent_work_unit_id is not None or unit.kind != "desktop.task" or unit.assigned_agent_id is None:
        return False
    if mission.source.type == MissionSourceType.MANUAL:
        return unit.assigned_adapter is not None and unit.assigned_adapter != "a2a.outbound"
    return mission.source.type == MissionSourceType.CHAT and unit.assigned_adapter == "function-calling"
