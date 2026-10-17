"""Build existing typed verifier plans after canonical policy normalization."""

from app.services.trusted_tests.policy import (
    TRUSTED_TEST_RUN_V1,
    TrustedTestEvaluationPlan,
)


def build_evaluation_plan(policy, digest, contract):
    # Imported at invocation time because the shared plan definitions live in
    # the resolver's compatibility module.
    from app.services.verification_policy_service import (
        ArtifactSetEvaluationPlan,
        BuildArtifactEvaluationPlan,
        SecurityScanEvaluationPlan,
        TestRunEvaluationPlan,
    )

    common = {"criterion_id": policy.criterion_id, "evaluator": policy.evaluator, "configuration_digest": digest}
    if policy.evaluator == TRUSTED_TEST_RUN_V1:
        return TrustedTestEvaluationPlan(**common, parameters=policy.parameters,
                                         contract_id=contract.id, contract_version=contract.version)
    if policy.evaluator == "test-run.v1":
        return TestRunEvaluationPlan(**common, minimum_test_results=policy.minimum_test_results,
                                     minimum_pass_rate=policy.minimum_pass_rate)
    if policy.evaluator == "build-artifact.v1":
        return BuildArtifactEvaluationPlan(**common, minimum_build_artifacts=policy.minimum_build_artifacts,
                                           max_artifact_size_bytes=policy.build_max_size_bytes)
    if policy.evaluator == "security-scan.v1":
        return SecurityScanEvaluationPlan(**common, minimum_scan_reports=policy.minimum_scan_reports,
                                          max_severity=policy.max_severity)
    return ArtifactSetEvaluationPlan(**common, minimum_artifacts=policy.minimum_artifacts,
                                     required_artifact_kinds=policy.required_artifact_kinds,
                                     optional_artifact_kinds=policy.optional_artifact_kinds,
                                     max_artifact_size_bytes=policy.max_artifact_size_bytes)
