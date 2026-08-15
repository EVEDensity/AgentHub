"""Deterministic execution of Mission Control verification policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain import ArtifactKind, EvidenceVerdict
from app.services.artifact_integrity_service import ArtifactByteVerification
from app.services.verification_policy_service import ArtifactSetEvaluationPlan

_ARTIFACT_SET_EVALUATOR = "artifact-set.v1"


class VerificationEvaluationError(ValueError):
    """Raised when a controlled evaluator cannot reproduce a PASS result."""


@dataclass(frozen=True, slots=True)
class VerificationEvaluationResult:
    criterion_id: str
    evaluator: str
    configuration_digest: str
    verdict: EvidenceVerdict
    artifact_verifications: tuple[ArtifactByteVerification, ...]


class VerificationArtifactMetadata(Protocol):
    """Artifact fields consumed by deterministic verification evaluators."""

    id: str
    kind: ArtifactKind
    digest: str
    size_bytes: int


class VerificationEvaluator(Protocol):
    def evaluate(
        self,
        plan: ArtifactSetEvaluationPlan,
        artifacts: tuple[VerificationArtifactMetadata, ...],
        byte_verifications: tuple[ArtifactByteVerification, ...],
    ) -> VerificationEvaluationResult: ...


class StrictVerificationEvaluator:
    """Execute only deterministic evaluators registered in this process."""

    def evaluate(
        self,
        plan: ArtifactSetEvaluationPlan,
        artifacts: tuple[VerificationArtifactMetadata, ...],
        byte_verifications: tuple[ArtifactByteVerification, ...],
    ) -> VerificationEvaluationResult:
        if plan.evaluator != _ARTIFACT_SET_EVALUATOR:
            raise VerificationEvaluationError("verification evaluator is not supported")
        if len(artifacts) < plan.minimum_artifacts:
            raise VerificationEvaluationError(
                "artifact-set evaluator minimum is not satisfied"
            )
        artifact_kinds = {artifact.kind.value for artifact in artifacts}
        if not set(plan.required_artifact_kinds) <= artifact_kinds:
            raise VerificationEvaluationError(
                "artifact-set evaluator required kinds are not satisfied"
            )

        canonical_verifications = canonicalize_artifact_byte_verifications(
            artifacts,
            byte_verifications,
        )

        return VerificationEvaluationResult(
            criterion_id=plan.criterion_id,
            evaluator=plan.evaluator,
            configuration_digest=plan.configuration_digest,
            verdict=EvidenceVerdict.PASS,
            artifact_verifications=canonical_verifications,
        )


def canonicalize_artifact_byte_verifications(
    artifacts: tuple[VerificationArtifactMetadata, ...],
    byte_verifications: tuple[ArtifactByteVerification, ...],
) -> tuple[ArtifactByteVerification, ...]:
    """Require exact byte-result closure and return Artifact-ID order."""
    artifacts_by_id = {artifact.id: artifact for artifact in artifacts}
    if len(artifacts_by_id) != len(artifacts):
        raise VerificationEvaluationError(
            "artifact byte verification requires unique Artifact IDs"
        )
    verifications_by_id = {
        verification.artifact_id: verification for verification in byte_verifications
    }
    if len(verifications_by_id) != len(byte_verifications):
        raise VerificationEvaluationError(
            "artifact byte verification results contain duplicate Artifact IDs"
        )
    if set(verifications_by_id) != set(artifacts_by_id):
        raise VerificationEvaluationError(
            "artifact byte verification results do not exactly match Artifacts"
        )

    canonical_verifications: list[ArtifactByteVerification] = []
    for artifact_id in sorted(artifacts_by_id):
        artifact = artifacts_by_id[artifact_id]
        verification = verifications_by_id[artifact_id]
        if verification.digest.lower() != artifact.digest.lower():
            raise VerificationEvaluationError(
                "artifact byte verification digest does not match metadata"
            )
        if verification.size_bytes != artifact.size_bytes:
            raise VerificationEvaluationError(
                "artifact byte verification size does not match metadata"
            )
        canonical_verifications.append(
            ArtifactByteVerification(
                artifact_id=artifact.id,
                digest=verification.digest.lower(),
                size_bytes=verification.size_bytes,
            )
        )
    return tuple(canonical_verifications)
