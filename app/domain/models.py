from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class FrozenDict(dict[str, Any]):
    """Dictionary that can be constructed normally but not mutated afterwards."""

    def _immutable(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("domain mappings are immutable")

    __delitem__ = _immutable
    __ior__ = _immutable
    __setitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


class FrozenList(list[Any]):
    """List that preserves JSON array serialization without allowing mutation."""

    def _immutable(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("domain sequences are immutable")

    __delitem__ = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable
    __setitem__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return FrozenDict({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return FrozenList(_deep_freeze(item) for item in value)
    return value


class DomainModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize exactly as the versioned public JSON contracts expect."""
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")


Identifier = Annotated[
    str,
    Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]
Digest = Annotated[str, Field(pattern=r"^sha256:[a-fA-F0-9]{64}$")]


class ActorType(str, Enum):
    HUMAN = "human"
    SERVICE = "service"
    AGENT = "agent"
    ADAPTER = "adapter"
    RUNNER = "runner"
    VERIFIER = "verifier"


class ActorRef(DomainModel):
    type: ActorType
    id: Identifier
    display_name: Annotated[str, Field(max_length=255)] | None = None


class ArtifactRef(DomainModel):
    id: Identifier
    digest: Digest


class ArtifactKind(str, Enum):
    DIFF = "diff"
    COMMIT = "commit"
    FILE = "file"
    LOG = "log"
    REPORT = "report"
    TEST_RESULT = "test-result"
    BUILD = "build"
    PULL_REQUEST = "pull-request"


class ArtifactRetention(str, Enum):
    EPHEMERAL = "ephemeral"
    MISSION = "mission"
    STANDARD = "standard"
    LEGAL_HOLD = "legal-hold"


class ArtifactSensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class Artifact(DomainModel):
    id: Identifier
    mission_id: Identifier
    work_unit_id: Identifier
    attempt: Annotated[int, Field(ge=1)]
    kind: ArtifactKind
    digest: Digest
    content_address: Annotated[str, Field(min_length=1, max_length=2048)]
    media_type: Annotated[str, Field(min_length=1, max_length=255)]
    size_bytes: Annotated[int, Field(ge=0)]
    source_repository: Annotated[str, Field(min_length=1, max_length=2048)] | None = None
    base_commit: Annotated[
        str, Field(pattern=r"^[a-fA-F0-9]{7,64}$")
    ] | None = None
    retention: ArtifactRetention = ArtifactRetention.MISSION
    sensitivity: ArtifactSensitivity = ArtifactSensitivity.INTERNAL
    created_by: ActorRef
    created_at: AwareDatetime


class MissionSourceType(str, Enum):
    MANUAL = "manual"
    ISSUE = "issue"
    API = "api"
    A2A = "a2a"
    A2A_INBOUND = "a2a.inbound"
    IMPORT = "import"


class MissionSource(DomainModel):
    type: MissionSourceType
    reference: Annotated[str, Field(max_length=2048)] | None = None
    external_id: Annotated[str, Field(max_length=255)] | None = None


class MissionStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    WAITING_DECISION = "WAITING_DECISION"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AggregateType(str, Enum):
    MISSION = "mission"
    MISSION_CONTRACT = "mission_contract"
    WORK_UNIT = "work_unit"
    ARTIFACT = "artifact"
    EVIDENCE = "evidence"
    DECISION = "decision"


class EventEnvelope(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )

    event_id: Identifier
    aggregate_type: AggregateType
    aggregate_id: Identifier
    sequence: Annotated[int, Field(ge=1)]
    event_type: Annotated[
        str, Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}$")
    ]
    actor: ActorRef
    occurred_at: AwareDatetime
    correlation_id: Identifier
    causation_id: Identifier | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    schema_version: Literal[1] = 1

    @field_validator("payload", mode="after")
    @classmethod
    def freeze_payload(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _deep_freeze(value)

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True, mode="json")


class Mission(DomainModel):
    id: Identifier
    workspace_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=255)]
    objective: Annotated[str, Field(min_length=1, max_length=10000)]
    source: MissionSource
    contract_id: Identifier
    status: MissionStatus
    plan_version: Annotated[int, Field(ge=0)] = 0
    created_by: ActorRef
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> Mission:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class RepositoryScope(DomainModel):
    repository: Annotated[str, Field(min_length=1, max_length=2048)]
    base_ref: Annotated[str, Field(min_length=1, max_length=255)]
    paths: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=1024)], ...],
        Field(min_length=1),
    ]
    write: bool = True


class CapabilityGrant(DomainModel):
    capability: Annotated[str, Field(min_length=1, max_length=255)]
    scope: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("scope", mode="after")
    @classmethod
    def freeze_scope(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _deep_freeze(value)


class Budgets(DomainModel):
    time_seconds: Annotated[int, Field(ge=1)]
    model_cost: Annotated[float, Field(ge=0)]
    retries: Annotated[int, Field(ge=0)]


class CriterionKind(str, Enum):
    COMMAND = "command"
    TEST = "test"
    BUILD = "build"
    SECURITY = "security"
    CONTRACT = "contract"
    MANUAL = "manual"


class AcceptanceCriterion(DomainModel):
    id: Identifier
    kind: CriterionKind
    description: Annotated[str, Field(min_length=1, max_length=2000)]
    required: bool
    configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("configuration", mode="after")
    @classmethod
    def freeze_configuration(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _deep_freeze(value)


class DecisionGate(DomainModel):
    id: Identifier
    trigger: Annotated[str, Field(min_length=1, max_length=255)]
    blocking: bool = True


class MissionContract(DomainModel):
    id: Identifier
    version: Annotated[int, Field(ge=1)]
    repository_scopes: tuple[RepositoryScope, ...]
    allowed_capabilities: tuple[CapabilityGrant, ...]
    budgets: Budgets
    acceptance_criteria: Annotated[tuple[AcceptanceCriterion, ...], Field(min_length=1)]
    decision_gates: tuple[DecisionGate, ...]
    forbidden_actions: tuple[Annotated[str, Field(min_length=1, max_length=255)], ...]
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_unique_entries(self) -> MissionContract:
        criterion_ids = [criterion.id for criterion in self.acceptance_criteria]
        gate_ids = [gate.id for gate in self.decision_gates]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("acceptance criterion ids must be unique")
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("decision gate ids must be unique")
        if len(self.forbidden_actions) != len(set(self.forbidden_actions)):
            raise ValueError("forbidden actions must be unique")
        return self


class OutputSpec(DomainModel):
    kind: Annotated[str, Field(min_length=1, max_length=255)]
    required: bool


class Lease(DomainModel):
    id: Identifier
    runner_id: Identifier
    expires_at: AwareDatetime


class WorkUnitStatus(str, Enum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    WAITING = "WAITING"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkUnit(DomainModel):
    id: Identifier
    mission_id: Identifier
    parent_work_unit_id: Identifier | None = None
    assigned_agent_id: Identifier | None = None
    kind: Annotated[str, Field(min_length=1, max_length=255)]
    dependencies: tuple[Identifier, ...]
    input_refs: tuple[ArtifactRef, ...]
    expected_outputs: tuple[OutputSpec, ...]
    required_capabilities: tuple[
        Annotated[str, Field(min_length=1, max_length=255)], ...
    ]
    assigned_adapter: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    status: WorkUnitStatus
    attempt: Annotated[int, Field(ge=0)] = 0
    lease: Lease | None = None

    @model_validator(mode="after")
    def validate_execution_state(self) -> WorkUnit:
        if self.parent_work_unit_id == self.id:
            raise ValueError("a work unit cannot delegate to itself")
        if self.id in self.dependencies:
            raise ValueError("a work unit cannot depend on itself")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("work unit dependencies must be unique")
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise ValueError("required capabilities must be unique")
        if (
            self.status in {WorkUnitStatus.LEASED, WorkUnitStatus.RUNNING}
            and self.lease is None
        ):
            raise ValueError(f"{self.status.value} work unit requires a lease")
        if (
            self.status not in {WorkUnitStatus.LEASED, WorkUnitStatus.RUNNING}
            and self.lease is not None
        ):
            raise ValueError(f"{self.status.value} work unit cannot retain a lease")
        return self


class VerifierRef(DomainModel):
    id: Identifier
    version: Annotated[str, Field(min_length=1, max_length=255)]
    configuration_digest: Digest | None = None


class EvidenceVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class Evidence(DomainModel):
    id: Identifier
    mission_id: Identifier
    work_unit_id: Identifier | None = None
    criterion_id: Identifier
    verifier: VerifierRef
    verdict: EvidenceVerdict
    artifact_refs: tuple[ArtifactRef, ...]
    summary: Annotated[str, Field(min_length=1, max_length=10000)]
    generated_at: AwareDatetime
    integrity_hash: Digest

    @model_validator(mode="after")
    def validate_artifact_refs(self) -> Evidence:
        artifact_ids = [artifact.id for artifact in self.artifact_refs]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("evidence artifact refs must be unique")
        return self


class DecisionStatus(str, Enum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class DecisionResolution(str, Enum):
    RETRY_WORK_UNIT = "RETRY_WORK_UNIT"
    FAIL_MISSION = "FAIL_MISSION"


class EvaluationPolicyReason(str, Enum):
    NO_APPLICABLE_POLICY = "no_applicable_policy"
    AMBIGUOUS_POLICY = "ambiguous_policy"
    INVALID_CONFIGURATION = "invalid_configuration"
    UNSUPPORTED_EVALUATOR = "unsupported_evaluator"
    ARTIFACT_REQUIREMENTS_NOT_MET = "artifact_requirements_not_met"


class Decision(DomainModel):
    id: Identifier
    mission_id: Identifier
    work_unit_id: Identifier
    attempt: Annotated[int, Field(ge=1)]
    context_digest: Digest
    reason_code: EvaluationPolicyReason
    criterion_ids: tuple[Identifier, ...]
    options: Annotated[tuple[DecisionResolution, ...], Field(min_length=1)]
    recommended_option: DecisionResolution
    risk_summary: Annotated[str, Field(min_length=1, max_length=2000)]
    status: DecisionStatus
    version: Annotated[int, Field(ge=1)] = 1
    requested_by: ActorRef
    requested_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    resolution: DecisionResolution | None = None
    rationale: Annotated[str, Field(min_length=1, max_length=10000)] | None = None
    resolved_by: ActorRef | None = None
    resolved_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Decision:
        if self.criterion_ids != tuple(sorted(set(self.criterion_ids))):
            raise ValueError("decision criterion IDs must be sorted and unique")
        if len(self.options) != len(set(self.options)):
            raise ValueError("decision options must be unique")
        if self.recommended_option not in self.options:
            raise ValueError("recommended option must be offered by the decision")
        if self.expires_at is not None and self.expires_at <= self.requested_at:
            raise ValueError("decision expiry must be later than requested_at")

        resolution_fields = (
            self.resolution,
            self.rationale,
            self.resolved_by,
            self.resolved_at,
        )
        if self.status == DecisionStatus.PENDING:
            if any(value is not None for value in resolution_fields):
                raise ValueError("pending decision cannot carry resolution fields")
            if self.version != 1:
                raise ValueError("pending decision must start at version 1")
            return self

        completion_fields = (self.rationale, self.resolved_by, self.resolved_at)
        if any(value is None for value in completion_fields):
            raise ValueError("closed decision requires complete resolution metadata")
        assert self.resolved_at is not None
        if self.resolved_at < self.requested_at:
            raise ValueError("decision resolution cannot predate its request")
        if self.version < 2:
            raise ValueError("closed decision version must be at least 2")
        if self.status in {DecisionStatus.CANCELLED, DecisionStatus.EXPIRED}:
            if self.resolution is not None:
                raise ValueError("unresolved closed decision cannot carry a resolution")
            if self.status == DecisionStatus.EXPIRED:
                if self.expires_at is None:
                    raise ValueError("expired decision requires expires_at")
                if self.resolved_at < self.expires_at:
                    raise ValueError("decision cannot expire before expires_at")
            return self
        assert self.resolution is not None
        if self.resolution not in self.options:
            raise ValueError("decision resolution was not an offered option")
        return self
