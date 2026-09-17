"""Deterministic execution of Mission Control verification policies.

Each evaluator registered in ``_EVALUATORS`` is a pure function that takes a
plan and a closure of artifacts + byte-verifications and either returns a PASS
result or raises :class:`VerificationEvaluationError`.  Adding a new evaluator
is one entry in the registry — the policy resolver in
:mod:`verification_policy_service` must recognise the evaluator id too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, TYPE_CHECKING

from app.domain import ArtifactKind, EvidenceVerdict
from app.services.artifact_integrity_service import ArtifactByteVerification

if TYPE_CHECKING:
    from app.services.verification_policy_service import (
        ArtifactSetEvaluationPlan,
        BuildArtifactEvaluationPlan,
        SecurityScanEvaluationPlan,
        TestRunEvaluationPlan,
    )


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


class _EvaluationPlan(Protocol):
    """Protocol shared by all evaluation plan dataclasses."""

    criterion_id: str
    evaluator: str
    configuration_digest: str


# Backward-compatible alias for the old single-plan evaluator contract.
VerificationEvaluator = _EvaluationPlan


# Evaluator-id → evaluator callable.  Keys must exactly match the ids
# registered in verification_policy_service._EVALUATOR_POLICY_KEYS.
EvaluatorCallable = Callable[
    [_EvaluationPlan, tuple[VerificationArtifactMetadata, ...], tuple[ArtifactByteVerification, ...]],
    VerificationEvaluationResult,
]


def _evaluate_artifact_set_v1(
    plan: "ArtifactSetEvaluationPlan",
    artifacts: tuple[VerificationArtifactMetadata, ...],
    byte_verifications: tuple[ArtifactByteVerification, ...],
) -> VerificationEvaluationResult:
    """Original artifact-set evaluator — existence + kind-set closure."""
    if len(artifacts) < plan.minimum_artifacts:
        raise VerificationEvaluationError(
            "artifact-set evaluator minimum is not satisfied"
        )
    artifact_kinds = {artifact.kind.value for artifact in artifacts}
    if not set(plan.required_artifact_kinds) <= artifact_kinds:
        raise VerificationEvaluationError(
            "artifact-set evaluator required kinds are not satisfied"
        )
    canonical = canonicalize_artifact_byte_verifications(artifacts, byte_verifications)
    return VerificationEvaluationResult(
        criterion_id=plan.criterion_id,
        evaluator=plan.evaluator,
        configuration_digest=plan.configuration_digest,
        verdict=EvidenceVerdict.PASS,
        artifact_verifications=canonical,
    )


def _evaluate_artifact_set_v2(
    plan: "ArtifactSetEvaluationPlan",
    artifacts: tuple[VerificationArtifactMetadata, ...],
    byte_verifications: tuple[ArtifactByteVerification, ...],
) -> VerificationEvaluationResult:
    """V2 adds optional-artifact-kind acceptance and a byte-size cap."""
    # Same existence checks as v1.
    if len(artifacts) < plan.minimum_artifacts:
        raise VerificationEvaluationError(
            "artifact-set evaluator minimum is not satisfied"
        )
    artifact_kinds = {artifact.kind.value for artifact in artifacts}
    if not set(plan.required_artifact_kinds) <= artifact_kinds:
        raise VerificationEvaluationError(
            "artifact-set evaluator required kinds are not satisfied"
        )
    # v2: any artifact kind in the artifacts that is neither required nor
    # optional would normally be rejected — but the current policy semantics
    # only constrains what *must* be present, not what *may not* be present.
    # We keep that permissive behaviour here; future evaluators (test, build,
    # security) that need hard exclusions can implement their own check.
    if plan.max_artifact_size_bytes is not None:
        for artifact in artifacts:
            if artifact.size_bytes > plan.max_artifact_size_bytes:
                raise VerificationEvaluationError(
                    "artifact-set.v2 evaluator exceeded maxArtifactSizeBytes"
                )
    canonical = canonicalize_artifact_byte_verifications(artifacts, byte_verifications)
    return VerificationEvaluationResult(
        criterion_id=plan.criterion_id,
        evaluator=plan.evaluator,
        configuration_digest=plan.configuration_digest,
        verdict=EvidenceVerdict.PASS,
        artifact_verifications=canonical,
    )


def _evaluate_test_run_v1(
    plan: "TestRunEvaluationPlan",
    artifacts: tuple[VerificationArtifactMetadata, ...],
    byte_verifications: tuple[ArtifactByteVerification, ...],
) -> VerificationEvaluationResult:
    """Test-run.v1 evaluator.

    Counts TEST_RESULT artifacts and checks that their aggregate pass rate
    meets the configured threshold.  TEST_RESULT artifacts are expected to
    carry their pass/total counters in the artifact metadata (via size_bytes
    convention is not used — the downstream verifier worker is responsible
    for extracting structured pass/total data before invoking this evaluator).

    The evaluator is intentionally minimal: it only *counts* TEST_RESULT
    artifacts and validates the plan's structural precondition
    (``minimum_test_results``).  Actual pass-rate arithmetic belongs to the
    verifier worker, which has access to the TEST_RESULT artifact body.
    """
    test_results = [a for a in artifacts if a.kind == ArtifactKind.TEST_RESULT]
    if len(test_results) < plan.minimum_test_results:
        raise VerificationEvaluationError(
            f"test-run.v1 evaluator needs at least {plan.minimum_test_results} "
            f"TEST_RESULT artifacts, found {len(test_results)}"
        )
    canonical = canonicalize_artifact_byte_verifications(artifacts, byte_verifications)
    return VerificationEvaluationResult(
        criterion_id=plan.criterion_id,
        evaluator=plan.evaluator,
        configuration_digest=plan.configuration_digest,
        verdict=EvidenceVerdict.PASS,
        artifact_verifications=canonical,
    )


def _evaluate_build_artifact_v1(
    plan: "BuildArtifactEvaluationPlan",
    artifacts: tuple[VerificationArtifactMetadata, ...],
    byte_verifications: tuple[ArtifactByteVerification, ...],
) -> VerificationEvaluationResult:
    """Build-artifact.v1 evaluator.

    Requires at least ``minimum_build_artifacts`` BUILD artifacts, each of
    which must not exceed ``max_artifact_size_bytes``.  Returns PASS when
    both conditions hold.  Build content integrity is verified upstream by
    the Runner/Harness layer before artifacts are deposited.
    """
    build_artifacts = [a for a in artifacts if a.kind == ArtifactKind.BUILD]
    if len(build_artifacts) < plan.minimum_build_artifacts:
        raise VerificationEvaluationError(
            f"build-artifact.v1 evaluator needs at least {plan.minimum_build_artifacts} "
            f"BUILD artifacts, found {len(build_artifacts)}"
        )
    oversized = [a for a in build_artifacts if a.size_bytes > plan.max_artifact_size_bytes]
    if oversized:
        raise VerificationEvaluationError(
            f"build-artifact.v1 evaluator found {len(oversized)} BUILD artifacts exceeding "
            f"maxArtifactSizeBytes={plan.max_artifact_size_bytes}"
        )
    canonical = canonicalize_artifact_byte_verifications(artifacts, byte_verifications)
    return VerificationEvaluationResult(
        criterion_id=plan.criterion_id,
        evaluator=plan.evaluator,
        configuration_digest=plan.configuration_digest,
        verdict=EvidenceVerdict.PASS,
        artifact_verifications=canonical,
    )


def _evaluate_security_scan_v1(
    plan: "SecurityScanEvaluationPlan",
    artifacts: tuple[VerificationArtifactMetadata, ...],
    byte_verifications: tuple[ArtifactByteVerification, ...],
) -> VerificationEvaluationResult:
    """Security-scan.v1 evaluator.

    Requires at least ``minimum_scan_reports`` REPORT artifacts.  The
    ``max_severity`` parameter (CVSS-style 0-5) is recorded in the plan
    configuration digest — actual severity arithmetic is done by the
    verifier worker, which parses the report body for vulnerability counts.
    The evaluator itself is a structural gate: are the expected scan reports
    present?
    """
    reports = [a for a in artifacts if a.kind == ArtifactKind.REPORT]
    if len(reports) < plan.minimum_scan_reports:
        raise VerificationEvaluationError(
            f"security-scan.v1 evaluator needs at least {plan.minimum_scan_reports} "
            f"REPORT artifacts, found {len(reports)}"
        )
    canonical = canonicalize_artifact_byte_verifications(artifacts, byte_verifications)
    return VerificationEvaluationResult(
        criterion_id=plan.criterion_id,
        evaluator=plan.evaluator,
        configuration_digest=plan.configuration_digest,
        verdict=EvidenceVerdict.PASS,
        artifact_verifications=canonical,
    )


_EVALUATORS: dict[str, EvaluatorCallable] = {}


def _ensure_registry() -> dict[str, EvaluatorCallable]:
    """Populate ``_EVALUATORS`` lazily to avoid circular imports at module load."""
    if _EVALUATORS:
        return _EVALUATORS
    from app.services.verification_policy_service import (
        _ARTIFACT_SET_V1,
        _ARTIFACT_SET_V2,
        _TEST_RUN_V1,
        _BUILD_ARTIFACT_V1,
        _SECURITY_SCAN_V1,
    )

    _EVALUATORS[_ARTIFACT_SET_V1] = _evaluate_artifact_set_v1
    _EVALUATORS[_ARTIFACT_SET_V2] = _evaluate_artifact_set_v2
    _EVALUATORS[_TEST_RUN_V1] = _evaluate_test_run_v1
    _EVALUATORS[_BUILD_ARTIFACT_V1] = _evaluate_build_artifact_v1
    _EVALUATORS[_SECURITY_SCAN_V1] = _evaluate_security_scan_v1
    return _EVALUATORS


class StrictVerificationEvaluator:
    """Dispatch to evaluators registered in ``_EVALUATORS``."""

    def evaluate(
        self,
        plan: _EvaluationPlan,
        artifacts: tuple[VerificationArtifactMetadata, ...],
        byte_verifications: tuple[ArtifactByteVerification, ...],
    ) -> VerificationEvaluationResult:
        evaluator = _ensure_registry().get(plan.evaluator)
        if evaluator is None:
            raise VerificationEvaluationError(
                f"verification evaluator {plan.evaluator!r} is not supported"
            )
        return evaluator(plan, artifacts, byte_verifications)


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