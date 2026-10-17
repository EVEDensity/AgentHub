"""Read-only native Session and bounded chat Mission directory contracts."""
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from app.domain import MissionStatus, SessionStatus
from app.domain.models import DomainModel, Identifier


class _HistoryProjection(DomainModel):
    def to_public_dict(self) -> dict:
        # End-of-directory cursor nulls are explicit fields in this wire contract.
        return self.model_dump(by_alias=True, mode="json")


class SessionDirectoryItem(_HistoryProjection):
    id: Identifier
    workspace_id: Identifier
    title: Annotated[str, Field(max_length=255)]
    status: SessionStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime


class SessionDirectory(_HistoryProjection):
    schema_version: Literal[1] = 1
    workspace_id: Identifier
    sessions: Annotated[list[SessionDirectoryItem], Field(max_length=50)]
    has_more: bool
    next_offset: Annotated[int, Field(ge=0)] | None


class SessionDetail(_HistoryProjection):
    schema_version: Literal[1] = 1
    workspace_id: Identifier
    session: SessionDirectoryItem


class SessionMissionItem(_HistoryProjection):
    id: Identifier
    workspace_id: Identifier
    session_id: Identifier
    title: Annotated[str, Field(max_length=255)]
    objective_preview: Annotated[str, Field(max_length=1000, description=(
        "At most 1,000 Unicode code points of the persisted Mission objective. "
        "Rules or archivist preprocessing may have changed this objective; "
        "this is a goal excerpt, not an original message receipt or complete transcript."
    ))]
    objective_truncated: bool
    status: MissionStatus
    created_at: AwareDatetime


class SessionMissionDirectory(_HistoryProjection):
    schema_version: Literal[1] = 1
    workspace_id: Identifier
    session_id: Identifier
    missions: Annotated[list[SessionMissionItem], Field(max_length=20)]
    has_more: bool
    next_before_id: Identifier | None
