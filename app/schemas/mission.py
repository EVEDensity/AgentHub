from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.domain import ArtifactRef, MissionContract, MissionSource, OutputSpec


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class MissionCreateRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    id: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    workspace_id: Annotated[str, Field(min_length=1, max_length=255)]
    title: Annotated[str, Field(min_length=1, max_length=255)]
    objective: Annotated[str, Field(min_length=1, max_length=10000)]
    source: MissionSource
    contract: MissionContract


class MissionListResponse(BaseModel):
    missions: list[dict]


class WorkUnitCreateRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    id: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    kind: Annotated[str, Field(min_length=1, max_length=255)]
    dependencies: list[Annotated[str, Field(min_length=1, max_length=255)]] = Field(
        default_factory=list
    )
    input_refs: list[ArtifactRef] = Field(default_factory=list)
    expected_outputs: list[OutputSpec] = Field(default_factory=list)
    required_capabilities: list[Annotated[str, Field(min_length=1, max_length=255)]] = (
        Field(default_factory=list)
    )
    assigned_adapter: Annotated[str, Field(min_length=1, max_length=255)] | None = None


class WorkUnitLeaseRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    lease_seconds: Annotated[int, Field(ge=1, le=3600)] = 300


class WorkUnitStartRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    lease_id: Annotated[str, Field(min_length=1, max_length=255)]
