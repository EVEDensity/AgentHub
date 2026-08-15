from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.domain import ArtifactRef, EvidenceVerdict, VerifierRef
from app.services.artifact_integrity_service import ArtifactByteVerification
from app.services.evidence_integrity_service import (
    EvidenceIntegrityError,
    EvidenceIntegrityMaterial,
    Sha256EvidenceIntegrityHasher,
)
from app.services.verification_evaluator_service import StrictVerificationEvaluator
from app.services.verification_policy_service import ArtifactSetEvaluationPlan
from tests.domain.factories import build_artifact


def build_material() -> EvidenceIntegrityMaterial:
    first = build_artifact(
        id="artifact-z",
        kind="report",
        digest="sha256:" + "b" * 64,
        size_bytes=20,
    )
    second = build_artifact(
        id="artifact-a",
        kind="diff",
        digest="sha256:" + "a" * 64,
        size_bytes=10,
    )
    observations = (
        ArtifactByteVerification(first.id, first.digest, first.size_bytes),
        ArtifactByteVerification(second.id, second.digest, second.size_bytes),
    )
    plan = ArtifactSetEvaluationPlan(
        criterion_id="tests",
        evaluator="artifact-set.v1",
        configuration_digest="sha256:" + "c" * 64,
        minimum_artifacts=2,
        required_artifact_kinds=("diff", "report"),
    )
    evaluation = StrictVerificationEvaluator().evaluate(
        plan,
        (first, second),
        observations,
    )
    return EvidenceIntegrityMaterial(
        evidence_id="evd-1",
        mission_id="mis-1",
        contract_id="contract-1",
        contract_version=3,
        work_unit_id="wu-1",
        work_unit_attempt=2,
        criterion_id="tests",
        verifier=VerifierRef(
            id="verifier-1",
            version="1.2.3",
            configuration_digest=plan.configuration_digest,
        ),
        verdict=EvidenceVerdict.PASS,
        artifact_refs=(
            ArtifactRef(id=first.id, digest=first.digest),
            ArtifactRef(id=second.id, digest=second.digest),
        ),
        artifacts=(first, second),
        byte_verifications=observations,
        evaluation=evaluation,
        summary="Artifacts satisfy the structural policy.",
        generated_at=datetime(2026, 8, 16, 8, 30, 0, 123456, tzinfo=timezone.utc),
    )


class EvidenceIntegrityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hasher = Sha256EvidenceIntegrityHasher()

    def test_hash_is_canonical_and_verifiable(self) -> None:
        material = build_material()
        reordered = replace(
            material,
            artifact_refs=tuple(reversed(material.artifact_refs)),
            artifacts=tuple(reversed(material.artifacts)),
            byte_verifications=tuple(reversed(material.byte_verifications)),
            generated_at=material.generated_at.astimezone(
                timezone(timedelta(hours=8))
            ),
        )

        integrity_hash = self.hasher.compute(material)

        self.assertRegex(integrity_hash, r"^sha256:[a-f0-9]{64}$")
        self.assertEqual(integrity_hash, self.hasher.compute(reordered))
        self.assertTrue(self.hasher.matches(material, integrity_hash))
        self.assertFalse(
            self.hasher.matches(material, "sha256:" + "0" * 64)
        )

    def test_material_changes_produce_different_hashes(self) -> None:
        material = build_material()
        baseline = self.hasher.compute(material)
        changes = (
            replace(material, summary="Different summary"),
            replace(material, work_unit_attempt=3),
            replace(material, evidence_id="evd-2"),
            replace(
                material,
                verifier=material.verifier.model_copy(
                    update={"version": "1.2.4"}
                ),
            ),
        )

        for changed in changes:
            with self.subTest(changed=changed):
                self.assertNotEqual(self.hasher.compute(changed), baseline)

    def test_pass_requires_matching_controlled_evaluation(self) -> None:
        material = build_material()
        mismatched = replace(
            material.evaluation,
            criterion_id="other",
        )
        cases = (
            (replace(material, evaluation=None), "requires"),
            (replace(material, evaluation=mismatched), "does not match"),
            (
                replace(
                    material,
                    verifier=material.verifier.model_copy(
                        update={"configuration_digest": "sha256:" + "d" * 64}
                    ),
                ),
                "does not match",
            ),
        )
        for changed, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(EvidenceIntegrityError, message),
            ):
                self.hasher.compute(changed)

    def test_non_pass_evidence_excludes_pass_evaluation(self) -> None:
        material = build_material()
        failed = replace(
            material,
            verdict=EvidenceVerdict.FAIL,
            evaluation=None,
        )

        self.assertRegex(self.hasher.compute(failed), r"^sha256:[a-f0-9]{64}$")
        with self.assertRaisesRegex(EvidenceIntegrityError, "non-PASS"):
            self.hasher.compute(replace(material, verdict=EvidenceVerdict.FAIL))

    def test_artifact_refs_and_byte_observations_must_close(self) -> None:
        material = build_material()
        cases = (
            (
                replace(material, artifact_refs=material.artifact_refs[:1]),
                "do not exactly match",
            ),
            (
                replace(material, byte_verifications=material.byte_verifications[:1]),
                "do not exactly match",
            ),
            (
                replace(
                    material,
                    artifact_refs=(
                        ArtifactRef(
                            id=material.artifact_refs[0].id,
                            digest="sha256:" + "e" * 64,
                        ),
                        material.artifact_refs[1],
                    ),
                ),
                "digest does not match",
            ),
        )
        for changed, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(EvidenceIntegrityError, message),
            ):
                self.hasher.compute(changed)

    def test_rejects_invalid_version_attempt_or_timestamp(self) -> None:
        material = build_material()
        cases = (
            (replace(material, contract_version=0), "contract version"),
            (replace(material, work_unit_attempt=-1), "attempt"),
            (
                replace(
                    material,
                    generated_at=material.generated_at.replace(tzinfo=None),
                ),
                "timezone-aware",
            ),
        )
        for changed, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(EvidenceIntegrityError, message),
            ):
                self.hasher.compute(changed)


if __name__ == "__main__":
    unittest.main()
