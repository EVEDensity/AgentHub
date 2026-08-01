from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


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


class MissionSourceType(str, Enum):
    MANUAL = "manual"
    ISSUE = "issue"
    API = "api"
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
    paths: Annotated[list[Annotated[str, Field(min_length=1, max_length=1024)]], Field(min_length=1)]
    write: bool = True


class CapabilityGrant(DomainModel):
    capability: Annotated[str, Field(min_length=1, max_length=255)]
    scope: dict[str, Any] = Field(default_factory=dict)


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
    configuration: dict[str, Any] = Field(default_factory=dict)


class DecisionGate(DomainModel):
    id: Identifier
    trigger: Annotated[str, Field(min_length=1, max_length=255)]
    blocking: bool = True


class MissionContract(DomainModel):
    id: Identifier
    version: Annotated[int, Field(ge=1)]
    repository_scopes: list[RepositoryScope]
    allowed_capabilities: list[CapabilityGrant]
    budgets: Budgets
    acceptance_criteria: Annotated[list[AcceptanceCriterion], Field(min_length=1)]
    decision_gates: list[DecisionGate]
    forbidden_actions: list[Annotated[str, Field(min_length=1, max_length=255)]]
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
    kind: Annotated[str, Field(min_length=1, max_length=255)]
    dependencies: list[Identifier]
    input_refs: list[ArtifactRef]
    expected_outputs: list[OutputSpec]
    required_capabilities: list[Annotated[str, Field(min_length=1, max_length=255)]]
    assigned_adapter: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    status: WorkUnitStatus
    attempt: Annotated[int, Field(ge=0)] = 0
    lease: Lease | None = None

    @model_validator(mode="after")
    def validate_execution_state(self) -> WorkUnit:
        if self.id in self.dependencies:
            raise ValueError("a work unit cannot depend on itself")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("work unit dependencies must be unique")
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise ValueError("required capabilities must be unique")
        if self.status in {WorkUnitStatus.LEASED, WorkUnitStatus.RUNNING} and self.lease is None:
            raise ValueError(f"{self.status.value} work unit requires a lease")
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
    artifact_refs: list[ArtifactRef]
    summary: Annotated[str, Field(min_length=1, max_length=10000)]
    generated_at: AwareDatetime
    integrity_hash: Digest

    @model_validator(mode="after")
    def validate_artifact_refs(self) -> Evidence:
        artifact_ids = [artifact.id for artifact in self.artifact_refs]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("evidence artifact refs must be unique")
        return self
