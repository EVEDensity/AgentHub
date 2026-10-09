"""Pure, one-way imports from legacy snapshots into Mission domain values."""

from datetime import UTC, datetime, tzinfo
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain import (
    ActorRef,
    Mission,
    MissionSource,
    MissionSourceType,
    MissionStatus,
)


class LegacyTaskMappingError(ValueError):
    """A legacy snapshot cannot be mapped without inventing domain facts."""


class LegacyTaskSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    session_id: str
    status: Literal["PENDING", "RUNNING", "SUCCESS", "FAILED"]
    created_at: datetime
    updated_at: datetime


def _utc(value: datetime, source_timezone: tzinfo | None) -> datetime:
    if value.utcoffset() is None:
        if source_timezone is None:
            raise LegacyTaskMappingError("naive timestamps require legacy_timezone")
        value = value.replace(tzinfo=source_timezone)
    return value.astimezone(UTC)


def map_legacy_task_to_mission(
    task: LegacyTaskSnapshot,
    *,
    workspace_id: str,
    title: str,
    objective: str,
    contract_id: str,
    created_by: ActorRef,
    legacy_timezone: tzinfo | None = None,
) -> Mission:
    created = _utc(task.created_at, legacy_timezone)
    updated = _utc(task.updated_at, legacy_timezone)
    if updated < created:
        raise LegacyTaskMappingError("updated_at cannot be earlier than created_at")
    status = {
        "PENDING": MissionStatus.READY,
        "RUNNING": MissionStatus.RUNNING,
        "SUCCESS": MissionStatus.VERIFYING,
        "FAILED": MissionStatus.FAILED,
    }[task.status]
    return Mission(
        id=task.id, workspace_id=workspace_id, title=title, objective=objective,
        source=MissionSource(
            type=MissionSourceType.IMPORT,
            reference=f"legacy-session:{task.session_id}", external_id=task.id,
        ),
        contract_id=contract_id, contract_version=1, status=status,
        created_by=created_by, created_at=created, updated_at=updated,
    )
