"""Canonical, server-owned integrity hashes for admitted Evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.domain import Artifact, ArtifactRef, EvidenceVerdict, VerifierRef
from app.services.artifact_integrity_service import ArtifactByteVerification
from app.services.verification_evaluator_service import (
    VerificationEvaluationError,
    VerificationEvaluationResult,
    canonicalize_artifact_byte_verifications,
)

_INTEGRITY_DOMAIN = "agenthub.evidence-integrity.v1"


class EvidenceIntegrityError(ValueError):
    """Raised when Evidence material cannot form a trustworthy envelope."""


@dataclass(frozen=True, slots=True)
class EvidenceIntegrityMaterial:
    evidence_id: str
    mission_id: str
    contract_id: str
    contract_version: int
    work_unit_id: str
    work_unit_attempt: int
    criterion_id: str
    verifier: VerifierRef
    verdict: EvidenceVerdict
    artifact_refs: tuple[ArtifactRef, ...]
    artifacts: tuple[Artifact, ...]
    byte_verifications: tuple[ArtifactByteVerification, ...]
    evaluation: VerificationEvaluationResult | None
    summary: str
    generated_at: datetime


class EvidenceIntegrityHasher(Protocol):
    def compute(self, material: EvidenceIntegrityMaterial) -> str: ...

    def matches(
        self,
        material: EvidenceIntegrityMaterial,
        integrity_hash: str,
    ) -> bool: ...


class Sha256EvidenceIntegrityHasher:
    """Hash a versioned canonical Evidence envelope with SHA-256."""

    def compute(self, material: EvidenceIntegrityMaterial) -> str:
        encoded = json.dumps(
            self._canonical_payload(material),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def matches(
        self,
        material: EvidenceIntegrityMaterial,
        integrity_hash: str,
    ) -> bool:
        return hmac.compare_digest(self.compute(material), integrity_hash.lower())

    def _canonical_payload(self, material: EvidenceIntegrityMaterial) -> dict:
        if material.contract_version < 1:
            raise EvidenceIntegrityError("contract version must be positive")
        if material.work_unit_attempt < 0:
            raise EvidenceIntegrityError("work unit attempt cannot be negative")
        if (
            material.generated_at.tzinfo is None
            or material.generated_at.utcoffset() is None
        ):
            raise EvidenceIntegrityError("Evidence generated_at must be timezone-aware")

        refs_by_id = {ref.id: ref for ref in material.artifact_refs}
        if len(refs_by_id) != len(material.artifact_refs):
            raise EvidenceIntegrityError("Evidence ArtifactRefs must be unique")
        artifacts_by_id = {artifact.id: artifact for artifact in material.artifacts}
        if set(refs_by_id) != set(artifacts_by_id):
            raise EvidenceIntegrityError(
                "Evidence ArtifactRefs do not exactly match evaluated Artifacts"
            )
        for artifact_id, artifact in artifacts_by_id.items():
            if refs_by_id[artifact_id].digest.lower() != artifact.digest.lower():
                raise EvidenceIntegrityError(
                    "Evidence ArtifactRef digest does not match evaluated Artifact"
                )

        try:
            observations = canonicalize_artifact_byte_verifications(
                material.artifacts,
                material.byte_verifications,
            )
        except VerificationEvaluationError as exc:
            raise EvidenceIntegrityError(str(exc)) from exc

        evaluation = material.evaluation
        if material.verdict == EvidenceVerdict.PASS:
            if evaluation is None:
                raise EvidenceIntegrityError(
                    "PASS Evidence requires a controlled evaluation result"
                )
            if (
                evaluation.verdict != material.verdict
                or evaluation.criterion_id != material.criterion_id
                or evaluation.configuration_digest
                != material.verifier.configuration_digest
                or evaluation.artifact_verifications != observations
            ):
                raise EvidenceIntegrityError(
                    "PASS evaluation does not match the Evidence envelope"
                )
        elif evaluation is not None:
            raise EvidenceIntegrityError(
                "non-PASS Evidence cannot include a PASS evaluation result"
            )

        generated_at = (
            material.generated_at.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        return {
            "domain": _INTEGRITY_DOMAIN,
            "evidence": {
                "id": material.evidence_id,
                "missionId": material.mission_id,
                "contract": {
                    "id": material.contract_id,
                    "version": material.contract_version,
                },
                "workUnit": {
                    "id": material.work_unit_id,
                    "attempt": material.work_unit_attempt,
                },
                "criterionId": material.criterion_id,
                "verifier": {
                    "id": material.verifier.id,
                    "version": material.verifier.version,
                    "configurationDigest": (
                        material.verifier.configuration_digest.lower()
                        if material.verifier.configuration_digest is not None
                        else None
                    ),
                },
                "verdict": material.verdict.value,
                "artifactRefs": [
                    {
                        "id": artifact_id,
                        "digest": refs_by_id[artifact_id].digest.lower(),
                    }
                    for artifact_id in sorted(refs_by_id)
                ],
                "artifactObservations": [
                    {
                        "id": observation.artifact_id,
                        "kind": artifacts_by_id[observation.artifact_id].kind.value,
                        "digest": observation.digest,
                        "sizeBytes": observation.size_bytes,
                    }
                    for observation in observations
                ],
                "evaluation": (
                    {
                        "criterionId": evaluation.criterion_id,
                        "evaluator": evaluation.evaluator,
                        "configurationDigest": evaluation.configuration_digest,
                        "verdict": evaluation.verdict.value,
                    }
                    if evaluation is not None
                    else None
                ),
                "summary": material.summary,
                "generatedAt": generated_at,
            },
        }
