"""Aggregate registered-byte, signed-provenance and test-conclusion checks."""

from __future__ import annotations

from app.domain import EvidenceVerdict
from app.services.trusted_tests.models import (
    ExecutionManifest,
    TrustedTestReceipt,
    artifact_id,
    content_digest,
    parse_document,
)
from app.services.trusted_tests.signing import load_trust_registry, verify_receipt
from app.services.verification_evaluator_service import (
    VerificationEvaluationResult,
    canonicalize_artifact_byte_verifications,
)
from app.services.verification_report_evaluator import evaluate_test_run
from app.services.verification_report_models import (
    TestRunReport,
    VerificationReportError,
)

_KINDS = {"patch": "diff", "report": "test-result", "log": "log", "receipt": "report", "manifest": "report"}


def _body(artifact, observations):
    value = observations[artifact.id].report_content
    if value is None or len(value) != artifact.size_bytes or content_digest(value) != artifact.digest:
        raise ValueError("trusted test document bytes are unavailable")
    return value


def _manifest(artifacts, observations):
    candidates = []
    for artifact in artifacts:
        if artifact.kind.value != "report":
            continue
        try:
            manifest = parse_document(_body(artifact, observations), ExecutionManifest)
        except VerificationReportError:
            continue
        candidates.append((artifact, manifest))
    if len(candidates) != 1:
        raise ValueError("trusted test bundle requires one execution manifest")
    return candidates[0]


def _bound_bundle(artifacts, observations):
    by_id = {artifact.id: artifact for artifact in artifacts}
    manifest_artifact, manifest = _manifest(artifacts, observations)
    references = {getattr(manifest, role).id for role in ("patch", "report", "log", "receipt")}
    if references | {manifest_artifact.id} != set(by_id) or len(by_id) != 5:
        raise ValueError("execution manifest does not close the exact Artifact set")
    bound = {role: by_id[getattr(manifest, role).id] for role in ("patch", "report", "log", "receipt")}
    bound["manifest"] = manifest_artifact
    expected_scope = (manifest.missionId, manifest.workUnitId, manifest.attempt)
    for role, artifact in bound.items():
        actual_scope = (artifact.mission_id, artifact.work_unit_id, artifact.attempt)
        if actual_scope != expected_scope or artifact.kind.value != _KINDS[role]:
            raise ValueError("trusted test Artifact scope or kind is invalid")
        if artifact.id != artifact_id(manifest.workUnitId, manifest.attempt, role, artifact.digest):
            raise ValueError("trusted test Artifact identity is not canonical")
        if role != "manifest" and getattr(manifest, role).digest != artifact.digest:
            raise ValueError("execution manifest Artifact digest differs")
    return manifest, bound


def _validate_receipt(plan, manifest, bound, observations, registry):
    receipt = parse_document(_body(bound["receipt"], observations), TrustedTestReceipt)
    verify_receipt(receipt, registry)
    fields = ("missionId", "workUnitId", "attempt", "contractId", "contractVersion", "configurationDigest",
              "executionMode", "inputSnapshotDigest", "candidateSnapshotDigest", "testProfileDigest")
    if any(getattr(receipt, name) != getattr(manifest, name) for name in fields):
        raise ValueError("producer receipt does not attest this execution manifest")
    policy = plan.parameters
    expected = (policy.expectedInputSnapshotDigest, policy.testProfileDigest, policy.producerId, policy.keyId, policy.executionMode)
    actual = (receipt.inputSnapshotDigest, receipt.testProfileDigest, receipt.producerId, receipt.keyId, receipt.executionMode)
    if actual != expected:
        raise ValueError("producer receipt does not satisfy the frozen Contract")
    if (receipt.contractId, receipt.contractVersion, receipt.configurationDigest) != (plan.contract_id, plan.contract_version, plan.configuration_digest):
        raise ValueError("producer receipt belongs to another Contract revision or acceptance policy")
    digests = (receipt.patchDigest, receipt.reportDigest, receipt.logDigest)
    if digests != tuple(bound[role].digest for role in ("patch", "report", "log")):
        raise ValueError("producer receipt does not attest the registered patch, report and log")
    report = parse_document(_body(bound["report"], observations), TestRunReport)
    if receipt.timedOut or receipt.exitCode != report.exitCode:
        raise ValueError("producer process result differs from the canonical test report")


def evaluate_trusted_test_run(plan, artifacts, byte_verifications, *, registry=None):
    observations = canonicalize_artifact_byte_verifications(artifacts, byte_verifications)
    try:
        manifest, bound = _bound_bundle(artifacts, {item.artifact_id: item for item in observations})
        _validate_receipt(plan, manifest, bound, {item.artifact_id: item for item in observations},
                          registry if registry is not None else load_trust_registry())
    except (ValueError, KeyError, TypeError, AttributeError):
        return VerificationEvaluationResult(
            criterion_id=plan.criterion_id, evaluator=plan.evaluator,
            configuration_digest=plan.configuration_digest, verdict=EvidenceVerdict.FAIL,
            artifact_verifications=observations,
            summary="Trusted-local test provenance, signature or execution binding is not satisfied.",
        )
    from app.services.verification_policy_service import TestRunEvaluationPlan

    semantic_plan = TestRunEvaluationPlan(
        criterion_id=plan.criterion_id, evaluator=plan.evaluator, configuration_digest=plan.configuration_digest,
        minimum_test_results=plan.parameters.minimumTestResults, minimum_pass_rate=plan.parameters.minimumPassRate,
    )
    return evaluate_test_run(semantic_plan, artifacts, observations)
