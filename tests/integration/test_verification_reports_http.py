from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx
from app.domain import ArtifactKind

from app.core.config import ArtifactStoreSettings
from app.services.artifact_integrity_service import ContentAddressedArtifactByteVerifier, ArtifactIntegrityError
from app.services.verifier_service import ControlledVerifier, MissionControlVerifierClient, VerificationPolicyUnavailableError
from app.services.verification_policy_service import StrictVerificationPolicyResolver
from tests.api.test_missions_api import FakeMissionRepository
from tests.domain.factories import build_contract, build_mission, build_work_unit
from tests.integration.test_verifier_service_http import _build_mission_control
from tests.services.verification_report_fixtures import make_test_report, security_report, report_artifact


def report_criterion(evaluator):
    parameters = {"minimumTestResults": 1, "minimumPassRate": 1.0} if evaluator == "test-run.v1" else {
        "minimumScanReports": 1, "maxSeverity": 3,
    }
    return {"id": "reports", "kind": "test" if evaluator == "test-run.v1" else "security", "description": "Registered report conclusion", "required": True,
            "configuration": {"evaluator": evaluator, "workUnitKinds": ["code_change"], **parameters}}


class VerificationReportsHttpTests(unittest.IsolatedAsyncioTestCase):
    def composition(self, root: Path, document, evaluator="test-run.v1"):
        repository = FakeMissionRepository()
        repository.contract = build_contract(acceptance_criteria=[report_criterion(evaluator)])
        repository.mission = build_mission(workspace_id="workspace-1", status="RUNNING")
        repository.work_units = [build_work_unit(status="VERIFYING", attempt=1, assigned_agent_id="executing-agent")]
        artifact, observation = report_artifact(document, "test-result" if evaluator == "test-run.v1" else "report")
        repository.artifacts = [artifact]
        path = root / "sha256" / artifact.digest.removeprefix("sha256:")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(observation.report_content)
        byte_verifier = ContentAddressedArtifactByteVerifier(ArtifactStoreSettings(backend="local", local_root=root, verify_max_bytes=2 * 1024 * 1024))
        return repository, _build_mission_control(repository, byte_verifier), byte_verifier

    async def test_independent_authenticated_verifier_reproduces_positive_and_negative_conclusions(self):
        cases = [(make_test_report(), "test-run.v1", "PASS"),
                 (make_test_report(("failed",), conclusion="failed", exitCode=1), "test-run.v1", "FAIL"),
                 (make_test_report(("errored",), conclusion="failed", exitCode=2), "test-run.v1", "FAIL"),
                 (b"unstructured legacy report", "test-run.v1", "FAIL"),
                 (make_test_report(attempt=2), "test-run.v1", "FAIL"),
                 (security_report(), "security-scan.v1", "PASS"),
                 (security_report((5,)), "security-scan.v1", "FAIL")]
        for document, evaluator, expected in cases:
            with self.subTest(evaluator=evaluator, verdict=expected), tempfile.TemporaryDirectory() as directory:
                repository, app, byte_verifier = self.composition(Path(directory), document, evaluator)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                    verifier = ControlledVerifier(MissionControlVerifierClient("http://mission-control.test", access_token="verifier-token", http_client=http),
                                                  byte_verifier=byte_verifier, verifier_id="verifier-1", verifier_version="report.v1")
                    result = await verifier.discover_and_verify("workspace-1")
                self.assertIsNotNone(result.evidence_id)
                self.assertEqual(repository.evidence[0].verdict.value, expected)
                self.assertEqual(repository.evidence[0].verifier.id, "verifier-1")
                target = "SUCCEEDED" if expected == "PASS" else "FAILED"
                self.assertEqual(repository.work_units[0].status.value, target)
                self.assertEqual(repository.mission.status.value, target)
                self.assertNotIn("cases", repository.evidence[0].to_public_dict())

    async def test_direct_pass_cannot_bypass_failed_reports_or_critical_findings(self):
        cases = [(make_test_report(("failed",), conclusion="failed", exitCode=1), "test-run.v1"),
                 (security_report((5,)), "security-scan.v1"), (b"missing conclusion fields", "test-run.v1")]
        for document, evaluator in cases:
            with self.subTest(evaluator=evaluator), tempfile.TemporaryDirectory() as directory:
                repository, app, _ = self.composition(Path(directory), document, evaluator)
                policy = StrictVerificationPolicyResolver().resolve(repository.contract, repository.work_units[0], tuple(repository.artifacts))
                request = {"criterionId": "reports", "verifierId": "verifier-1", "verifierVersion": "report.v1", "verdict": "PASS",
                           "configurationDigest": policy.plan.configuration_digest,
                           "artifactRefs": [{"id": repository.artifacts[0].id, "digest": repository.artifacts[0].digest}], "summary": "passing claim"}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                    response = await http.post("http://mission-control.test/api/v1/missions/mis-1/work-units/wu-1/verify",
                                               headers={"Authorization": "Bearer verifier-token"}, json=request)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertFalse(repository.evidence)
                self.assertEqual(repository.work_units[0].status.value, "VERIFYING")

    async def test_unrelated_token_or_verifier_identity_cannot_record_result(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, app, _ = self.composition(Path(directory), make_test_report())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                bad = await http.post("http://mission-control.test/api/v1/missions/verification-work-items/discover",
                                      headers={"Authorization": "Bearer runner-token"}, json={"workspaceId": "workspace-1"})
                request = {"criterionId": "reports", "verifierId": "executing-agent", "verifierVersion": "report.v1",
                           "verdict": "FAIL", "artifactRefs": [{"id": repository.artifacts[0].id, "digest": repository.artifacts[0].digest}], "summary": "failure"}
                wrong_id = await http.post("http://mission-control.test/api/v1/missions/mis-1/work-units/wu-1/verify",
                                           headers={"Authorization": "Bearer verifier-token"}, json=request)
            self.assertEqual(bad.status_code, 401)
            self.assertEqual(wrong_id.status_code, 403)
            self.assertFalse(repository.evidence)

    async def test_missing_report_opens_existing_inconclusive_decision_without_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, app, verifier_bytes = self.composition(Path(directory), make_test_report())
            repository.artifacts[0] = repository.artifacts[0].model_copy(update={"kind": ArtifactKind.DIFF})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                verifier = ControlledVerifier(MissionControlVerifierClient("http://mission-control.test", access_token="verifier-token", http_client=http),
                                              byte_verifier=verifier_bytes, verifier_id="verifier-1", verifier_version="report.v1")
                with self.assertRaises(VerificationPolicyUnavailableError):
                    await verifier.discover_and_verify("workspace-1")
            self.assertFalse(repository.evidence)
            self.assertEqual(repository.mission.status.value, "WAITING_DECISION")
            self.assertEqual(repository.decisions[0].status.value, "PENDING")

    async def test_actual_report_byte_tampering_is_rejected_without_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, app, byte_verifier = self.composition(root, make_test_report())
            path = root / "sha256" / repository.artifacts[0].digest.removeprefix("sha256:")
            path.write_bytes(b"tampered report")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                verifier = ControlledVerifier(MissionControlVerifierClient("http://mission-control.test", access_token="verifier-token", http_client=http),
                                              byte_verifier=byte_verifier, verifier_id="verifier-1", verifier_version="report.v1")
                with self.assertRaises(ArtifactIntegrityError):
                    await verifier.discover_and_verify("workspace-1")
            self.assertFalse(repository.evidence)
            self.assertEqual(repository.work_units[0].status.value, "VERIFYING")
