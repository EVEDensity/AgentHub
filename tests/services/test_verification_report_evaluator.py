from __future__ import annotations

import unittest
from dataclasses import replace

from app.domain import EvidenceVerdict
from app.services.verification_evaluator_service import StrictVerificationEvaluator, VerificationEvaluationError
from app.services.verification_policy_service import TestRunEvaluationPlan as RunEvaluationPlan, SecurityScanEvaluationPlan
from tests.services.verification_report_fixtures import make_test_report, security_report, report_artifact


def plan(evaluator="test-run.v1", **updates):
    common = {"criterion_id": "reports", "evaluator": evaluator, "configuration_digest": "sha256:" + "c" * 64}
    if evaluator == "test-run.v1":
        return RunEvaluationPlan(**common, **{"minimum_test_results": 1, "minimum_pass_rate": 1.0, **updates})
    return SecurityScanEvaluationPlan(**common, **{"minimum_scan_reports": 1, "max_severity": 0, **updates})


class ReportEvaluatorTests(unittest.TestCase):
    def evaluate(self, document, evaluator="test-run.v1", **configuration):
        artifact, observation = report_artifact(document, "test-result" if evaluator == "test-run.v1" else "report")
        return StrictVerificationEvaluator().evaluate(plan(evaluator, **configuration), (artifact,), (observation,))

    def test_positive_reports_and_aggregate_pass_threshold(self):
        self.assertEqual(self.evaluate(make_test_report()).verdict, EvidenceVerdict.PASS)
        self.assertEqual(self.evaluate(security_report(), "security-scan.v1").verdict, EvidenceVerdict.PASS)
        mixed = make_test_report(("passed", "skipped"))
        self.assertEqual(self.evaluate(mixed, minimum_pass_rate=0.5).verdict, EvidenceVerdict.PASS)
        self.assertEqual(self.evaluate(mixed, minimum_pass_rate=0.6).verdict, EvidenceVerdict.FAIL)

    def test_failed_error_and_inconclusive_test_results_never_pass(self):
        cases = [make_test_report(("failed",), conclusion="failed", exitCode=1),
                 make_test_report(("errored",), conclusion="failed", exitCode=2),
                 make_test_report(conclusion="inconclusive", exitCode=None),
                 make_test_report((), conclusion="inconclusive", exitCode=None),
                 make_test_report(("skipped",)), make_test_report(())]
        for document in cases:
            with self.subTest(document=document):
                self.assertEqual(self.evaluate(document, minimum_pass_rate=0).verdict, EvidenceVerdict.FAIL)

    def test_counter_claims_cannot_hide_failing_cases(self):
        report = make_test_report(("passed", "failed"))
        report.update(passed=2, failed=0)
        self.assertEqual(self.evaluate(report).verdict, EvidenceVerdict.FAIL)

    def test_security_threshold_uses_actual_findings_and_scanner_result(self):
        self.assertEqual(self.evaluate(security_report((2,)), "security-scan.v1", max_severity=2).verdict, EvidenceVerdict.PASS)
        documents = [security_report((5,)), security_report((5,), findingCount=0, maximumSeverity=0),
                     security_report(conclusion="failed", exitCode=1), security_report(conclusion="inconclusive", exitCode=None)]
        for document in documents:
            with self.subTest(document=document):
                self.assertEqual(self.evaluate(document, "security-scan.v1", max_severity=4).verdict, EvidenceVerdict.FAIL)

    def test_malformed_legacy_duplicate_keys_and_wrong_attempt_reports_fail(self):
        documents = [b"legacy plain report", b'{"conclusion":"failed","conclusion":"passed"}',
                     make_test_report(schemaVersion=True), make_test_report(attempt=True), make_test_report(passed=True),
                     make_test_report(total=42), make_test_report(unknown="field"), make_test_report(missionId="other"),
                     make_test_report(workUnitId="other"), make_test_report(attempt=2), make_test_report(reportType="security-scan"),
                     make_test_report(exitCode=float("nan")), make_test_report(cases=[])]
        for document in documents:
            with self.subTest(document=document):
                self.assertEqual(self.evaluate(document).verdict, EvidenceVerdict.FAIL)

    def test_missing_body_and_tampered_body_fail_and_invalid_byte_closure_is_rejected(self):
        artifact, observation = report_artifact(make_test_report())
        evaluator = StrictVerificationEvaluator()
        for content in (None, b"wrong bytes"):
            result = evaluator.evaluate(plan(), (artifact,), (replace(observation, report_content=content),))
            self.assertEqual(result.verdict, EvidenceVerdict.FAIL)
        with self.assertRaises(VerificationEvaluationError):
            evaluator.evaluate(plan(), (artifact,), (replace(observation, digest="sha256:" + "b" * 64),))
        self.assertEqual(evaluator.evaluate(plan(), (), ()).verdict, EvidenceVerdict.FAIL)

    def test_duplicate_cases_and_findings_are_rejected(self):
        test = make_test_report(("passed", "passed"))
        test["cases"][1]["id"] = test["cases"][0]["id"]
        self.assertEqual(self.evaluate(test).verdict, EvidenceVerdict.FAIL)
        security = security_report((1, 1))
        security["findings"][1]["id"] = security["findings"][0]["id"]
        self.assertEqual(self.evaluate(security, "security-scan.v1", max_severity=2).verdict, EvidenceVerdict.FAIL)

    def test_aggregate_report_records_are_unique_and_threshold_is_global(self):
        first = make_test_report(("passed", "skipped"))
        second = make_test_report()
        artifacts_and_bytes = [report_artifact(document, id=f"report-{index}") for index, document in enumerate((first, second))]
        artifacts, observations = tuple(zip(*artifacts_and_bytes))
        result = StrictVerificationEvaluator().evaluate(plan(minimum_test_results=2, minimum_pass_rate=0.6), artifacts, observations)
        self.assertEqual(result.verdict, EvidenceVerdict.FAIL)
        second["cases"][0]["id"] = "separate-case"
        artifacts_and_bytes[1] = report_artifact(second, id="report-1")
        artifacts, observations = tuple(zip(*artifacts_and_bytes))
        result = StrictVerificationEvaluator().evaluate(plan(minimum_test_results=2, minimum_pass_rate=0.6), artifacts, observations)
        self.assertEqual(result.verdict, EvidenceVerdict.PASS)
        result = StrictVerificationEvaluator().evaluate(plan(minimum_test_results=2, minimum_pass_rate=0.7), artifacts, observations)
        self.assertEqual(result.verdict, EvidenceVerdict.FAIL)
