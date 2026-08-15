from __future__ import annotations

import unittest

from app.domain import AcceptanceCriterion
from app.services.verification_policy_service import (
    EvaluationPolicyDecision,
    EvaluationPolicyReason,
    StrictVerificationPolicyResolver,
)
from tests.domain.factories import build_artifact, build_contract, build_work_unit


def build_criterion(
    criterion_id: str = "artifact-set",
    *,
    configuration: dict | None = None,
) -> AcceptanceCriterion:
    return AcceptanceCriterion(
        id=criterion_id,
        kind="contract",
        description="Required artifacts are present and independently verifiable.",
        required=True,
        configuration=configuration or {},
    )


class VerificationPolicyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = StrictVerificationPolicyResolver()

    def test_unconfigured_criterion_is_inconclusive(self) -> None:
        decision = self.resolver.resolve(
            build_contract(acceptance_criteria=[build_criterion()]),
            build_work_unit(kind="code_change", status="VERIFYING", attempt=1),
            (build_artifact(),),
        )

        self.assertEqual(
            decision.to_public_dict(),
            {
                "status": "inconclusive",
                "reasonCode": "no_applicable_policy",
            },
        )

    def test_explicit_artifact_set_policy_resolves_canonical_ready_plan(self) -> None:
        artifacts = (
            build_artifact(id="artifact-diff", kind="diff"),
            build_artifact(id="artifact-report", kind="report"),
        )
        first = self.resolver.resolve(
            build_contract(
                acceptance_criteria=[
                    build_criterion(
                        configuration={
                            "evaluator": "artifact-set.v1",
                            "workUnitKinds": ["other", "a2a.delegate"],
                            "minimumArtifacts": 2,
                            "requiredArtifactKinds": ["report", "diff"],
                        }
                    )
                ]
            ),
            build_work_unit(kind="a2a.delegate", status="VERIFYING", attempt=1),
            artifacts,
        )
        reordered = self.resolver.resolve(
            build_contract(
                acceptance_criteria=[
                    build_criterion(
                        configuration={
                            "requiredArtifactKinds": ["diff", "report"],
                            "minimumArtifacts": 2,
                            "workUnitKinds": ["a2a.delegate", "other"],
                            "evaluator": "artifact-set.v1",
                        }
                    )
                ]
            ),
            build_work_unit(kind="a2a.delegate", status="VERIFYING", attempt=1),
            artifacts,
        )

        first_public = first.to_public_dict()
        self.assertEqual(first_public["status"], "ready")
        self.assertEqual(first_public["criterionId"], "artifact-set")
        self.assertEqual(first_public["evaluator"], "artifact-set.v1")
        self.assertRegex(first_public["configurationDigest"], r"^sha256:[a-f0-9]{64}$")
        self.assertEqual(
            first_public["parameters"],
            {
                "minimumArtifacts": 2,
                "requiredArtifactKinds": ["diff", "report"],
            },
        )
        self.assertEqual(
            first_public["configurationDigest"],
            reordered.to_public_dict()["configurationDigest"],
        )
        self.assertNotIn("verdict", first_public)

    def test_unknown_or_malformed_policy_configuration_is_inconclusive(self) -> None:
        configurations = (
            {
                "evaluator": "artifact-set.v1",
                "workUnitKinds": ["code_change"],
                "minimumArtifacts": 1,
            },
            {
                "evaluator": "artifact-set.v1",
                "workUnitKinds": ["code_change"],
                "minimumArtifacts": 1,
                "requiredArtifactKinds": [],
                "unknown": True,
            },
            {
                "evaluator": "artifact-set.v1",
                "workUnitKinds": ["code_change", "code_change"],
                "minimumArtifacts": 1,
                "requiredArtifactKinds": [],
            },
            {
                "evaluator": "artifact-set.v1",
                "workUnitKinds": ["code_change"],
                "minimumArtifacts": True,
                "requiredArtifactKinds": [],
            },
        )
        for configuration in configurations:
            with self.subTest(configuration=configuration):
                decision = self.resolver.resolve(
                    build_contract(
                        acceptance_criteria=[
                            build_criterion(configuration=configuration)
                        ]
                    ),
                    build_work_unit(
                        kind="code_change",
                        status="VERIFYING",
                        attempt=1,
                    ),
                    (build_artifact(),),
                )
                self.assertEqual(
                    decision.reason,
                    EvaluationPolicyReason.INVALID_CONFIGURATION,
                )

    def test_unknown_evaluator_is_inconclusive(self) -> None:
        decision = self.resolver.resolve(
            build_contract(
                acceptance_criteria=[
                    build_criterion(
                        configuration={
                            "evaluator": "model-judge.v1",
                            "workUnitKinds": ["code_change"],
                            "minimumArtifacts": 1,
                            "requiredArtifactKinds": [],
                        }
                    )
                ]
            ),
            build_work_unit(kind="code_change", status="VERIFYING", attempt=1),
            (build_artifact(),),
        )

        self.assertEqual(
            decision.reason,
            EvaluationPolicyReason.UNSUPPORTED_EVALUATOR,
        )

    def test_multiple_matching_policies_are_inconclusive(self) -> None:
        configuration = {
            "evaluator": "artifact-set.v1",
            "workUnitKinds": ["code_change"],
            "minimumArtifacts": 1,
            "requiredArtifactKinds": [],
        }
        decision = self.resolver.resolve(
            build_contract(
                acceptance_criteria=[
                    build_criterion("first", configuration=configuration),
                    build_criterion("second", configuration=configuration),
                ]
            ),
            build_work_unit(kind="code_change", status="VERIFYING", attempt=1),
            (build_artifact(),),
        )

        self.assertEqual(
            decision.reason,
            EvaluationPolicyReason.AMBIGUOUS_POLICY,
        )

    def test_unsatisfied_artifact_requirements_are_inconclusive(self) -> None:
        decision = self.resolver.resolve(
            build_contract(
                acceptance_criteria=[
                    build_criterion(
                        configuration={
                            "evaluator": "artifact-set.v1",
                            "workUnitKinds": ["a2a.delegate"],
                            "minimumArtifacts": 2,
                            "requiredArtifactKinds": ["report"],
                        }
                    )
                ]
            ),
            build_work_unit(kind="a2a.delegate", status="VERIFYING", attempt=1),
            (build_artifact(kind="diff"),),
        )

        self.assertEqual(
            decision.reason,
            EvaluationPolicyReason.ARTIFACT_REQUIREMENTS_NOT_MET,
        )

    def test_decision_requires_exactly_one_plan_or_reason(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            EvaluationPolicyDecision()


if __name__ == "__main__":
    unittest.main()
