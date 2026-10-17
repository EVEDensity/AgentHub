"""Convert the discriminator-validated discovery projection to evaluator input."""

from app.services.trusted_tests.policy import (
    TRUSTED_TEST_RUN_V1,
    TrustedTestEvaluationPlan,
)
from app.services.verification_policy_service import (
    ArtifactSetEvaluationPlan,
    BuildArtifactEvaluationPlan,
    SecurityScanEvaluationPlan,
    TestRunEvaluationPlan,
)


def build_discovered_plan(policy, contract=None):
    common = {"criterion_id": policy.criterion_id, "evaluator": policy.evaluator,
              "configuration_digest": policy.configuration_digest}
    values = policy.parameters
    if policy.evaluator == TRUSTED_TEST_RUN_V1:
        if contract is None:
            raise ValueError("trusted test discovery requires the actual Contract identity")
        return TrustedTestEvaluationPlan(**common, parameters=values, contract_id=contract.id, contract_version=contract.version)
    if policy.evaluator == "artifact-set.v1":
        return ArtifactSetEvaluationPlan(**common, minimum_artifacts=values.minimum_artifacts,
                                         required_artifact_kinds=tuple(kind.value for kind in values.required_artifact_kinds))
    if policy.evaluator == "artifact-set.v2":
        return ArtifactSetEvaluationPlan(**common, minimum_artifacts=values.minimum_artifacts,
                                         required_artifact_kinds=tuple(kind.value for kind in values.required_artifact_kinds),
                                         optional_artifact_kinds=tuple(kind.value for kind in values.optional_artifact_kinds),
                                         max_artifact_size_bytes=values.max_artifact_size_bytes)
    if policy.evaluator == "test-run.v1":
        return TestRunEvaluationPlan(**common, minimum_test_results=values.minimum_test_results,
                                     minimum_pass_rate=values.minimum_pass_rate)
    if policy.evaluator == "build-artifact.v1":
        return BuildArtifactEvaluationPlan(**common, minimum_build_artifacts=values.minimum_build_artifacts,
                                           max_artifact_size_bytes=values.max_artifact_size_bytes)
    return SecurityScanEvaluationPlan(**common, minimum_scan_reports=values.minimum_scan_reports,
                                      max_severity=values.max_severity)
