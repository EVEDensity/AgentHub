from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone, tzinfo
from enum import Enum
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.domain import ActorRef, Mission, MissionSource, MissionStatus


class LegacyTaskMappingError(ValueError):
    """Raised when legacy data cannot be mapped without guessing semantics."""


class LegacyTaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class LegacyTaskSnapshot(BaseModel):
    """Stable legacy fields accepted by the one-way Mission compatibility boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, Field(min_length=1, max_length=255)]
    session_id: Annotated[str, Field(min_length=1, max_length=255)]
    status: LegacyTaskStatus
    created_at: datetime
    updated_at: datetime


LEGACY_MISSION_STATUSES: Mapping[LegacyTaskStatus, MissionStatus] = MappingProxyType(
    {
        LegacyTaskStatus.PENDING: MissionStatus.READY,
        LegacyTaskStatus.RUNNING: MissionStatus.RUNNING,
        # Legacy success has no verifiable Evidence, so it cannot prove completion.
        LegacyTaskStatus.SUCCESS: MissionStatus.VERIFYING,
        LegacyTaskStatus.FAILED: MissionStatus.FAILED,
    }
)


def _normalize_timestamp(value: datetime, legacy_timezone: tzinfo | None) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        if legacy_timezone is None:
            raise LegacyTaskMappingError(
                "naive legacy timestamps require an explicit legacy_timezone"
            )
        value = value.replace(tzinfo=legacy_timezone)
        if value.utcoffset() is None:
            raise LegacyTaskMappingError("legacy_timezone must define a UTC offset")
    return value.astimezone(timezone.utc)


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
    """Project a legacy Task into a Mission without interpreting its DAG JSON."""

    created_at = _normalize_timestamp(task.created_at, legacy_timezone)
    updated_at = _normalize_timestamp(task.updated_at, legacy_timezone)
    if updated_at < created_at:
        raise LegacyTaskMappingError(
            "legacy task updated_at cannot be earlier than created_at"
        )

    return Mission(
        id=task.id,
        workspace_id=workspace_id,
        title=title,
        objective=objective,
        source=MissionSource(
            type="import",
            reference=f"legacy-session:{task.session_id}",
            external_id=task.id,
        ),
        contract_id=contract_id,
        status=LEGACY_MISSION_STATUSES[task.status],
        plan_version=0,
        created_by=created_by,
        created_at=created_at,
        updated_at=updated_at,
    )
