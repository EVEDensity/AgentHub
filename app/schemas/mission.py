from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain import (
    ArtifactKind,
    ArtifactRef,
    ArtifactRetention,
    ArtifactSensitivity,
    MissionContract,
    MissionSource,
    OutputSpec,
)


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


class WorkUnitDelegationRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    id: Annotated[str, Field(min_length=1, max_length=255)]
    kind: Annotated[str, Field(min_length=1, max_length=255)] = "agent_delegation"
    agent_id: Annotated[str, Field(min_length=1, max_length=255)]
    input_refs: Annotated[list[ArtifactRef], Field(min_length=1)]
    expected_outputs: list[OutputSpec] = Field(default_factory=list)
    required_capabilities: list[Annotated[str, Field(min_length=1, max_length=255)]] = (
        Field(default_factory=list)
    )
    lease_id: Annotated[str, Field(min_length=1, max_length=255)]


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


class WorkUnitHeartbeatRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    lease_id: Annotated[str, Field(min_length=1, max_length=255)]
    lease_seconds: Annotated[int, Field(ge=1, le=3600)] = 300


class WorkUnitExecutionRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    lease_id: Annotated[str, Field(min_length=1, max_length=255)]
    reason: Annotated[str, Field(min_length=1, max_length=2000)] | None = None


class WorkUnitCompletionRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    lease_id: Annotated[str, Field(min_length=1, max_length=255)]
    artifact_refs: Annotated[list[ArtifactRef], Field(min_length=1)]


class ArtifactCreateRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    id: Annotated[str, Field(min_length=1, max_length=255)]
    lease_id: Annotated[str, Field(min_length=1, max_length=255)]
    kind: ArtifactKind
    digest: Annotated[str, Field(pattern=r"^sha256:[a-fA-F0-9]{64}$")]
    content_address: Annotated[str, Field(min_length=1, max_length=2048)]
    media_type: Annotated[str, Field(min_length=1, max_length=255)]
    size_bytes: Annotated[int, Field(ge=0)]
    source_repository: Annotated[str, Field(min_length=1, max_length=2048)] | None = None
    base_commit: Annotated[
        str, Field(pattern=r"^[a-fA-F0-9]{7,64}$")
    ] | None = None
    retention: ArtifactRetention = ArtifactRetention.MISSION
    sensitivity: ArtifactSensitivity = ArtifactSensitivity.INTERNAL


class WorkUnitVerificationRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    criterion_id: Annotated[str, Field(min_length=1, max_length=255)]
    verifier_id: Annotated[str, Field(min_length=1, max_length=255)]
    verifier_version: Annotated[str, Field(min_length=1, max_length=255)]
    configuration_digest: Annotated[
        str, Field(pattern=r"^sha256:[a-fA-F0-9]{64}$")
    ] | None = None
    verdict: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    artifact_refs: Annotated[list[ArtifactRef], Field(min_length=1)]
    summary: Annotated[str, Field(min_length=1, max_length=10000)]
    integrity_hash: Annotated[str, Field(pattern=r"^sha256:[a-fA-F0-9]{64}$")]
