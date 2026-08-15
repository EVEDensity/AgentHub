"""Independent, fail-closed verification coordination."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.domain import (
    AcceptanceCriterion,
    ArtifactKind,
    ArtifactRef,
    ArtifactSensitivity,
    Evidence,
    EvidenceVerdict,
    Mission,
    OutputSpec,
    WorkUnit,
    WorkUnitStatus,
)
from app.services.artifact_integrity_service import (
    ArtifactByteVerifier,
)
from app.services.verification_evaluator_service import (
    StrictVerificationEvaluator,
    VerificationEvaluator,
)
from app.services.verification_policy_service import ArtifactSetEvaluationPlan


class VerifierError(RuntimeError):
    """Base error for one independent verification attempt."""


class VerifierControlError(VerifierError):
    """Raised when Mission Control cannot complete a verifier command."""


class VerifierProtocolError(VerifierError):
    """Raised when Mission Control returns an invalid verifier response."""


class VerificationPolicyUnavailableError(VerifierError):
    """Raised when automated verification has no attributable ready policy."""


class MissionControlVerifierPort(Protocol):
    async def discover_verification_work(
        self,
        workspace_id: str,
    ) -> dict[str, Any]: ...

    async def submit_verification(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        submission: VerificationSubmission,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class VerificationSubmission:
    criterion_id: str
    verifier_id: str
    verifier_version: str
    configuration_digest: str | None
    verdict: EvidenceVerdict
    artifact_refs: tuple[ArtifactRef, ...]
    summary: str

    def to_request_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "criterionId": self.criterion_id,
            "verifierId": self.verifier_id,
            "verifierVersion": self.verifier_version,
            "verdict": self.verdict.value,
            "artifactRefs": [item.to_public_dict() for item in self.artifact_refs],
            "summary": self.summary,
        }
        if self.configuration_digest is not None:
            payload["configurationDigest"] = self.configuration_digest
        return payload


class MissionControlVerifierClient:
    """HTTP adapter limited to verifier discovery and Evidence admission."""

    def __init__(
        self,
        base_url: str,
        *,
        access_token: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        if not normalized_url:
            raise ValueError("base_url must be non-empty")
        self._base_url = normalized_url
        self._access_token = access_token
        self._http_client = http_client

    async def discover_verification_work(
        self,
        workspace_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/missions/verification-work-items/discover",
            json={"workspaceId": workspace_id},
        )

    async def submit_verification(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        submission: VerificationSubmission,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/verify",
            json=submission.to_request_dict(),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any],
    ) -> dict[str, Any]:
        headers = (
            {"Authorization": f"Bearer {self._access_token}"}
            if self._access_token
            else {}
        )
        try:
            if self._http_client is not None:
                response = await self._http_client.request(
                    method,
                    self._base_url + path,
                    headers=headers,
                    json=json,
                )
            else:
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    response = await client.request(
                        method,
                        self._base_url + path,
                        headers=headers,
                        json=json,
                    )
        except httpx.HTTPError as exc:
            raise VerifierControlError(
                f"Mission Control request failed: {method} {path}"
            ) from exc
        if response.is_error:
            detail: object = response.text[:500]
            try:
                payload = response.json()
                if isinstance(payload, dict) and "detail" in payload:
                    detail = payload["detail"]
            except ValueError:
                pass
            raise VerifierControlError(
                f"Mission Control rejected {method} {path}: {detail}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise VerifierProtocolError(
                f"Mission Control returned invalid JSON: {method} {path}"
            ) from exc
        if not isinstance(payload, dict):
            raise VerifierProtocolError(
                f"Mission Control returned an invalid response: {method} {path}"
            )
        return payload


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class _Projection(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class _MissionProjection(_Projection):
    id: Annotated[str, Field(min_length=1, max_length=255)]
    title: Annotated[str, Field(min_length=1, max_length=255)]
    objective: Annotated[str, Field(min_length=1, max_length=10000)]


class _ContractProjection(_Projection):
    id: Annotated[str, Field(min_length=1, max_length=255)]
    version: Annotated[int, Field(ge=1)]
    acceptance_criteria: tuple[AcceptanceCriterion, ...]


class _WorkUnitProjection(_Projection):
    id: Annotated[str, Field(min_length=1, max_length=255)]
    kind: Annotated[str, Field(min_length=1, max_length=255)]
    input_refs: tuple[ArtifactRef, ...]
    expected_outputs: tuple[OutputSpec, ...]
    status: Literal["VERIFYING"]
    attempt: Annotated[int, Field(ge=1)]


class _ArtifactProjection(_Projection):
    id: Annotated[str, Field(min_length=1, max_length=255)]
    attempt: Annotated[int, Field(ge=1)]
    kind: ArtifactKind
    digest: Annotated[str, Field(pattern=r"^sha256:[a-fA-F0-9]{64}$")]
    content_address: Annotated[str, Field(min_length=1, max_length=2048)]
    media_type: Annotated[str, Field(min_length=1, max_length=255)]
    size_bytes: Annotated[int, Field(ge=0)]
    source_repository: Annotated[str, Field(min_length=1, max_length=2048)] | None = (
        None
    )
    base_commit: (
        Annotated[
            str,
            Field(pattern=r"^[a-fA-F0-9]{7,64}$"),
        ]
        | None
    ) = None
    sensitivity: ArtifactSensitivity


class _ArtifactSetParameters(_Projection):
    minimum_artifacts: Annotated[int, Field(ge=1, le=200)]
    required_artifact_kinds: tuple[ArtifactKind, ...]

    @model_validator(mode="after")
    def validate_unique_kinds(self) -> _ArtifactSetParameters:
        if len(self.required_artifact_kinds) != len(set(self.required_artifact_kinds)):
            raise ValueError("required artifact kinds must be unique")
        return self


class _ReadyEvaluationPolicy(_Projection):
    status: Literal["ready"]
    criterion_id: Annotated[str, Field(min_length=1, max_length=255)]
    evaluator: Literal["artifact-set.v1"]
    configuration_digest: Annotated[
        str,
        Field(pattern=r"^sha256:[a-f0-9]{64}$"),
    ]
    parameters: _ArtifactSetParameters


class _InconclusiveEvaluationPolicy(_Projection):
    status: Literal["inconclusive"]
    reason_code: Literal[
        "no_applicable_policy",
        "ambiguous_policy",
        "invalid_configuration",
        "unsupported_evaluator",
        "artifact_requirements_not_met",
    ]


_EvaluationPolicy = Annotated[
    _ReadyEvaluationPolicy | _InconclusiveEvaluationPolicy,
    Field(discriminator="status"),
]


class _VerificationContext(_Projection):
    version: Literal[2]
    mission: _MissionProjection
    contract: _ContractProjection
    work_unit: _WorkUnitProjection
    artifacts: Annotated[
        tuple[_ArtifactProjection, ...], Field(min_length=1, max_length=200)
    ]
    evaluation_policy: _EvaluationPolicy

    @model_validator(mode="after")
    def validate_artifact_scope(self) -> _VerificationContext:
        artifact_ids = [artifact.id for artifact in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("verification Artifacts must be unique")
        if any(
            artifact.attempt != self.work_unit.attempt for artifact in self.artifacts
        ):
            raise ValueError("verification Artifact attempt does not match WorkUnit")
        criterion_ids = {
            criterion.id for criterion in self.contract.acceptance_criteria
        }
        policy = self.evaluation_policy
        if (
            isinstance(policy, _ReadyEvaluationPolicy)
            and policy.criterion_id not in criterion_ids
        ):
            raise ValueError("evaluation policy criterion is absent from Contract")
        return self


class _DiscoveryResponse(_Projection):
    discovery_status: Literal["ready", "idle"]
    verification_context: _VerificationContext | None

    @model_validator(mode="after")
    def validate_status_context_pair(self) -> _DiscoveryResponse:
        if (self.verification_context is not None) != (
            self.discovery_status == "ready"
        ):
            raise ValueError("discovery status and context are inconsistent")
        return self


class _VerificationAdmissionResponse(_Projection):
    evidence: Evidence
    work_unit: WorkUnit
    mission: Mission


@dataclass(frozen=True, slots=True)
class VerificationArtifact:
    id: str
    mission_id: str
    work_unit_id: str
    attempt: int
    kind: ArtifactKind
    digest: str
    content_address: str
    media_type: str
    size_bytes: int
    sensitivity: ArtifactSensitivity
    source_repository: str | None = None
    base_commit: str | None = None


class VerifierPollStatus(str, Enum):
    IDLE = "idle"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class VerifierPollResult:
    status: VerifierPollStatus
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        if (self.evidence_id is not None) != (
            self.status == VerifierPollStatus.VERIFIED
        ):
            raise ValueError("verifier poll status and Evidence ID are inconsistent")


class ControlledVerifier:
    """Evaluate one discovered item without owning durable scheduling state."""

    def __init__(
        self,
        control: MissionControlVerifierPort,
        *,
        byte_verifier: ArtifactByteVerifier,
        verifier_id: str,
        verifier_version: str,
        evaluator: VerificationEvaluator | None = None,
    ) -> None:
        if not verifier_id.strip():
            raise ValueError("verifier_id must be non-empty")
        if not verifier_version.strip():
            raise ValueError("verifier_version must be non-empty")
        self._control = control
        self._byte_verifier = byte_verifier
        self._verifier_id = verifier_id
        self._verifier_version = verifier_version
        self._evaluator = evaluator or StrictVerificationEvaluator()

    async def discover_and_verify(self, workspace_id: str) -> VerifierPollResult:
        if not workspace_id.strip():
            raise ValueError("workspace_id must be non-empty")
        raw_discovery = await self._control.discover_verification_work(workspace_id)
        discovery = _validate_response(_DiscoveryResponse, raw_discovery)
        if discovery.verification_context is None:
            return VerifierPollResult(status=VerifierPollStatus.IDLE)

        context = discovery.verification_context
        policy = context.evaluation_policy
        if isinstance(policy, _InconclusiveEvaluationPolicy):
            raise VerificationPolicyUnavailableError(
                f"verification policy is inconclusive: {policy.reason_code}"
            )

        artifacts = tuple(
            VerificationArtifact(
                id=artifact.id,
                mission_id=context.mission.id,
                work_unit_id=context.work_unit.id,
                attempt=artifact.attempt,
                kind=artifact.kind,
                digest=artifact.digest.lower(),
                content_address=artifact.content_address,
                media_type=artifact.media_type,
                size_bytes=artifact.size_bytes,
                sensitivity=artifact.sensitivity,
                source_repository=artifact.source_repository,
                base_commit=artifact.base_commit,
            )
            for artifact in context.artifacts
        )
        byte_verifications = await self._byte_verifier.verify_all(artifacts)
        plan = ArtifactSetEvaluationPlan(
            criterion_id=policy.criterion_id,
            evaluator=policy.evaluator,
            configuration_digest=policy.configuration_digest,
            minimum_artifacts=policy.parameters.minimum_artifacts,
            required_artifact_kinds=tuple(
                kind.value for kind in policy.parameters.required_artifact_kinds
            ),
        )
        evaluation = self._evaluator.evaluate(
            plan,
            artifacts,
            tuple(byte_verifications),
        )
        if evaluation.verdict != EvidenceVerdict.PASS:
            raise VerifierProtocolError("controlled evaluator did not return PASS")

        artifact_refs = tuple(
            ArtifactRef(id=artifact.id, digest=artifact.digest)
            for artifact in artifacts
        )
        submission = VerificationSubmission(
            criterion_id=evaluation.criterion_id,
            verifier_id=self._verifier_id,
            verifier_version=self._verifier_version,
            configuration_digest=evaluation.configuration_digest,
            verdict=evaluation.verdict,
            artifact_refs=artifact_refs,
            summary=(
                f"{evaluation.evaluator} verified {len(artifacts)} Artifact(s) "
                f"for WorkUnit attempt {context.work_unit.attempt}."
            ),
        )
        raw_admission = await self._control.submit_verification(
            context.mission.id,
            context.work_unit.id,
            submission=submission,
        )
        admission = _validate_response(_VerificationAdmissionResponse, raw_admission)
        self._validate_admission(
            admission,
            mission_id=context.mission.id,
            work_unit_id=context.work_unit.id,
            submission=submission,
        )
        return VerifierPollResult(
            status=VerifierPollStatus.VERIFIED,
            evidence_id=admission.evidence.id,
        )

    def _validate_admission(
        self,
        admission: _VerificationAdmissionResponse,
        *,
        mission_id: str,
        work_unit_id: str,
        submission: VerificationSubmission,
    ) -> None:
        evidence = admission.evidence
        expected_refs = sorted(
            (item.id, item.digest.lower()) for item in submission.artifact_refs
        )
        actual_refs = sorted(
            (item.id, item.digest.lower()) for item in evidence.artifact_refs
        )
        if (
            evidence.mission_id != mission_id
            or evidence.work_unit_id != work_unit_id
            or evidence.criterion_id != submission.criterion_id
            or evidence.verdict != submission.verdict
            or evidence.verifier.id != submission.verifier_id
            or evidence.verifier.version != submission.verifier_version
            or evidence.verifier.configuration_digest != submission.configuration_digest
            or actual_refs != expected_refs
            or admission.work_unit.id != work_unit_id
            or admission.work_unit.status != WorkUnitStatus.SUCCEEDED
            or admission.mission.id != mission_id
        ):
            raise VerifierProtocolError(
                "Mission Control verification response does not match submission"
            )


def _validate_response(model: type[_Projection], payload: Mapping[str, Any]) -> Any:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise VerifierProtocolError(
            "Mission Control returned an invalid verifier contract"
        ) from exc


__all__ = [
    "ControlledVerifier",
    "MissionControlVerifierClient",
    "MissionControlVerifierPort",
    "VerificationArtifact",
    "VerificationPolicyUnavailableError",
    "VerificationSubmission",
    "VerifierControlError",
    "VerifierError",
    "VerifierPollResult",
    "VerifierPollStatus",
    "VerifierProtocolError",
]
