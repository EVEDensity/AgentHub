"""Compatibility boundaries for legacy AgentHub interfaces."""

from app.compat.legacy_tasks import (
    LegacyTaskMappingError,
    LegacyTaskSnapshot,
    LegacyTaskStatus,
    map_legacy_task_to_mission,
)

__all__ = [
    "LegacyTaskMappingError",
    "LegacyTaskSnapshot",
    "LegacyTaskStatus",
    "map_legacy_task_to_mission",
]
