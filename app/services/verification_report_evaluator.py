"""Controlled conclusions from exact, integrity-verified report bytes."""

from __future__ import annotations

import hashlib

from app.domain import ArtifactKind, EvidenceVerdict
from app.services.verification_evaluator_service import VerificationEvaluationResult, canonicalize_artifact_byte_verifications
from app.services.verification_report_models import (
    VerificationReportError, TestRunReport, SecurityScanReport, parse_report,
)


def _read_report(artifact, observation, model):
    content = observation.report_content
    if content is None or len(content) != artifact.size_bytes:
        raise VerificationReportError("bounded report content is unavailable")
    if "sha256:" + hashlib.sha256(content).hexdigest() != artifact.digest.lower():
        raise VerificationReportError("report content does not match registered digest")
    report = parse_report(content, model)
    identity = (report.missionId, report.workUnitId, report.attempt)
    expected = (getattr(artifact, "mission_id", None), getattr(artifact, "work_unit_id", None), getattr(artifact, "attempt", None))
    if identity != expected:
        raise VerificationReportError("report belongs to a different Mission or WorkUnit attempt")
    return report


def _reports(artifacts, observations, kind, minimum, model):
    selected = [artifact for artifact in artifacts if artifact.kind == kind]
    if len(selected) < minimum:
        raise VerificationReportError("required reports are missing")
    by_id = {observation.artifact_id: observation for observation in observations}
    return [_read_report(artifact, by_id[artifact.id], model) for artifact in selected]


def _result(plan, observations, verdict, summary):
    return VerificationEvaluationResult(
        criterion_id=plan.criterion_id, evaluator=plan.evaluator, configuration_digest=plan.configuration_digest,
        verdict=verdict, artifact_verifications=observations, summary=summary,
    )


def _case_totals(reports):
    cases = [case for report in reports for case in report.cases]
    if len({case.id for case in cases}) != len(cases):
        raise VerificationReportError("aggregate test reports contain duplicate case identities")
    return len(cases), sum(case.status == "passed" for case in cases)


def evaluate_test_run(plan, artifacts, byte_verifications):
    observations = canonicalize_artifact_byte_verifications(artifacts, byte_verifications)
    try:
        reports = _reports(artifacts, observations, ArtifactKind.TEST_RESULT, plan.minimum_test_results, TestRunReport)
        total, passed = _case_totals(reports)
    except VerificationReportError:
        return _result(plan, observations, EvidenceVerdict.FAIL, "Test report contract or attempt identity is not satisfied.")
    if any(report.conclusion != "passed" or report.exitCode != 0 or report.failed or report.errored for report in reports):
        return _result(plan, observations, EvidenceVerdict.FAIL, "Test reports contain an unsuccessful or inconclusive result.")
    if total == 0 or passed / total < plan.minimum_pass_rate:
        return _result(plan, observations, EvidenceVerdict.FAIL, "Test report aggregate pass rate does not satisfy the Contract.")
    return _result(plan, observations, EvidenceVerdict.PASS, f"Test reports passed: {passed}/{total} tests meet the Contract pass rate.")


def evaluate_security_scan(plan, artifacts, byte_verifications):
    observations = canonicalize_artifact_byte_verifications(artifacts, byte_verifications)
    try:
        reports = _reports(artifacts, observations, ArtifactKind.REPORT, plan.minimum_scan_reports, SecurityScanReport)
    except VerificationReportError:
        return _result(plan, observations, EvidenceVerdict.FAIL, "Security report contract or attempt identity is not satisfied.")
    if any(report.conclusion != "passed" or report.exitCode != 0 for report in reports):
        return _result(plan, observations, EvidenceVerdict.FAIL, "Security reports contain an unsuccessful or inconclusive scan result.")
    maximum = max((finding.severity for report in reports for finding in report.findings), default=0)
    if maximum > plan.max_severity:
        return _result(plan, observations, EvidenceVerdict.FAIL, "Security report findings exceed the Contract severity limit.")
    return _result(plan, observations, EvidenceVerdict.PASS, f"Security reports passed: maximum severity {maximum} meets the Contract limit.")
