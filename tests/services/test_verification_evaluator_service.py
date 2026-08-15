from __future__ import annotations

import unittest

from app.domain import EvidenceVerdict
from app.services.artifact_integrity_service import ArtifactByteVerification
from app.services.verification_evaluator_service import (
    StrictVerificationEvaluator,
    VerificationEvaluationError,
)
from app.services.verification_policy_service import ArtifactSetEvaluationPlan
from tests.domain.factories import build_artifact


def build_plan(**updates: object) -> ArtifactSetEvaluationPlan:
    values: dict[str, object] = {
        "criterion_id": "tests",
        "evaluator": "artifact-set.v1",
        "configuration_digest": "sha256:" + "c" * 64,
        "minimum_artifacts": 1,
        "required_artifact_kinds": ("diff",),
    }
    values.update(updates)
    return ArtifactSetEvaluationPlan(**values)


def verification_for(
    artifact_id: str,
    *,
    digest: str,
    size_bytes: int,
) -> ArtifactByteVerification:
    return ArtifactByteVerification(
        artifact_id=artifact_id,
        digest=digest,
        size_bytes=size_bytes,
    )


class VerificationEvaluatorServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = StrictVerificationEvaluator()

    def test_artifact_set_evaluator_returns_canonical_pass_result(self) -> None:
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
        plan = build_plan(
            minimum_artifacts=2,
            required_artifact_kinds=("diff", "report"),
        )

        result = self.evaluator.evaluate(
            plan,
            (first, second),
            (
                verification_for(
                    first.id,
                    digest=first.digest.upper(),
                    size_bytes=first.size_bytes,
                ),
                verification_for(
                    second.id,
                    digest=second.digest,
                    size_bytes=second.size_bytes,
                ),
            ),
        )

        self.assertEqual(result.criterion_id, plan.criterion_id)
        self.assertEqual(result.evaluator, "artifact-set.v1")
        self.assertEqual(result.configuration_digest, plan.configuration_digest)
        self.assertEqual(result.verdict, EvidenceVerdict.PASS)
        self.assertEqual(
            [item.artifact_id for item in result.artifact_verifications],
            ["artifact-a", "artifact-z"],
        )
        self.assertEqual(
            result.artifact_verifications[1].digest,
            first.digest,
        )

    def test_rejects_unsupported_evaluator_or_unsatisfied_plan(self) -> None:
        artifact = build_artifact(kind="diff")
        verification = verification_for(
            artifact.id,
            digest=artifact.digest,
            size_bytes=artifact.size_bytes,
        )
        cases = (
            (
                build_plan(evaluator="model-judge.v1"),
                (artifact,),
                "not supported",
            ),
            (
                build_plan(minimum_artifacts=2),
                (artifact,),
                "minimum",
            ),
            (
                build_plan(required_artifact_kinds=("report",)),
                (artifact,),
                "required kinds",
            ),
        )
        for plan, artifacts, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(VerificationEvaluationError, message),
            ):
                self.evaluator.evaluate(plan, artifacts, (verification,))

    def test_requires_exact_unique_byte_verification_closure(self) -> None:
        artifact = build_artifact()
        valid = verification_for(
            artifact.id,
            digest=artifact.digest,
            size_bytes=artifact.size_bytes,
        )
        extra = verification_for(
            "artifact-extra",
            digest=artifact.digest,
            size_bytes=artifact.size_bytes,
        )
        cases = (
            ((), "exactly match"),
            ((valid, extra), "exactly match"),
            ((valid, valid), "duplicate"),
        )
        for verifications, message in cases:
            with (
                self.subTest(message=message, count=len(verifications)),
                self.assertRaisesRegex(VerificationEvaluationError, message),
            ):
                self.evaluator.evaluate(
                    build_plan(required_artifact_kinds=()),
                    (artifact,),
                    verifications,
                )

    def test_rejects_digest_or_size_mismatch(self) -> None:
        artifact = build_artifact()
        cases = (
            (
                verification_for(
                    artifact.id,
                    digest="sha256:" + "b" * 64,
                    size_bytes=artifact.size_bytes,
                ),
                "digest",
            ),
            (
                verification_for(
                    artifact.id,
                    digest=artifact.digest,
                    size_bytes=artifact.size_bytes + 1,
                ),
                "size",
            ),
        )
        for verification, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(VerificationEvaluationError, message),
            ):
                self.evaluator.evaluate(
                    build_plan(required_artifact_kinds=()),
                    (artifact,),
                    (verification,),
                )


if __name__ == "__main__":
    unittest.main()
