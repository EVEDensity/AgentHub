"""Authenticated independent verification of signed registered bytes on SQLite."""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import httpx

from app.core.config import ArtifactStoreSettings
from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.sqlite_pool import SQLitePool
from app.repositories import MissionRepository
from app.services.artifact_integrity_service import (
    ArtifactIntegrityError,
    ContentAddressedArtifactByteVerifier,
)
from app.services.trusted_tests.models import canonical_bytes, content_digest
from app.services.trusted_tests.signing import TRUST_KEYS_FILE_ENV
from app.services.verification_policy_service import StrictVerificationPolicyResolver
from app.services.verifier_service import (
    ControlledVerifier,
    MissionControlVerifierClient,
)
from tests.domain.factories import build_artifact, build_contract, build_mission, build_work_unit
from tests.integration.test_verifier_service_http import _build_mission_control
from tests.services.trusted_test_fixtures import (
    INPUT_DIGEST,
    signed_bundle,
    trust_registry,
    trusted_criterion,
)
from tests.services.verification_report_fixtures import make_test_report


class TrustedTestVerificationSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.pool = SQLitePool(self.root / "control.sqlite3")
        await self.pool.initialize()
        self.addAsyncCleanup(self.pool.close)
        async with self.pool.acquire() as connection:
            self.connection = connection
        await _create_mission_control_plane_sqlite(connection)

        @asynccontextmanager
        async def transaction():
            async with connection.transaction():
                yield connection

        self.repository = MissionRepository(execute=connection.execute, fetch_one=connection.fetchrow,
                                            fetch_all=connection.fetch, transaction_factory=transaction)
        self.byte_verifier = ContentAddressedArtifactByteVerifier(ArtifactStoreSettings(
            backend="local", local_root=self.root, verify_max_bytes=2 * 1024 * 1024,
        ))
        keys = self.root / "operator-keys.json"
        keys.write_bytes(canonical_bytes(trust_registry()))
        environment = patch.dict(os.environ, {TRUST_KEYS_FILE_ENV: str(keys)})
        environment.start()
        self.addCleanup(environment.stop)

    async def seed(self, *, criterion=None, **bundle_updates):
        self.contract = build_contract(acceptance_criteria=[criterion or trusted_criterion()])
        self.unit = build_work_unit(kind="desktop.task", status="VERIFYING", attempt=1, assigned_agent_id="executing-agent")
        self.artifacts, _, bodies = signed_bundle(**bundle_updates)
        await self.repository.add_contract_lineage(self.contract.id, "workspace-1")
        await self.repository.add_contract(self.contract)
        await self.repository.add_mission(build_mission(workspace_id="workspace-1", status="RUNNING"))
        await self.repository.add_work_unit(self.unit)
        await self.store_artifacts(self.artifacts, bodies)

    async def store_artifacts(self, artifacts, bodies):
        for artifact in artifacts:
            path = self.root / "sha256" / artifact.digest[7:]
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(bodies[artifact.id])
            if await self.repository.get_artifact(artifact.id) is None:
                await self.repository.add_artifact(artifact)

    def additional_failed_report(self):
        body = canonical_bytes(make_test_report(("failed",), conclusion="failed", exitCode=1))
        digest = content_digest(body)
        artifact = build_artifact(id="additional-failed-report", kind="test-result", digest=digest,
                                  content_address="local:sha256/" + digest[7:], size_bytes=len(body))
        return artifact, {artifact.id: body}

    async def assert_subset_pass_refused(self):
        response = await self.forged_pass()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("complete registered current-attempt", response.text)
        self.assertFalse(await self.repository.list_evidence("mis-1"))
        self.assertEqual((await self.repository.get_work_unit("wu-1")).status.value, "VERIFYING")
        self.assertEqual((await self.repository.get_mission("mis-1")).status.value, "RUNNING")

    async def evaluate(self):
        app = _build_mission_control(self.repository, self.byte_verifier)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
            verifier = ControlledVerifier(
                MissionControlVerifierClient("http://control.test", access_token="verifier-token", http_client=http),
                byte_verifier=self.byte_verifier, verifier_id="verifier-1", verifier_version="trusted-tests.v1",
            )
            return await verifier.discover_and_verify("workspace-1")

    async def forged_pass(self):
        plan = StrictVerificationPolicyResolver().resolve(self.contract, self.unit, self.artifacts).plan
        app = _build_mission_control(self.repository, self.byte_verifier)
        request = {"criterionId": plan.criterion_id, "verifierId": "verifier-1", "verifierVersion": "trusted-tests.v1",
                   "configurationDigest": plan.configuration_digest, "verdict": "PASS", "summary": "forged passing claim",
                   "artifactRefs": [{"id": artifact.id, "digest": artifact.digest} for artifact in self.artifacts]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
            return await http.post("http://control.test/api/v1/missions/mis-1/work-units/wu-1/verify",
                                   headers={"Authorization": "Bearer verifier-token"}, json=request)

    async def test_signed_bundle_commits_real_evidence_and_success(self):
        await self.seed()
        result = await self.evaluate()
        evidence = await self.repository.list_evidence("mis-1")
        self.assertEqual([item.id for item in evidence], [result.evidence_id])
        self.assertEqual(evidence[0].verdict.value, "PASS")
        self.assertEqual(len(evidence[0].artifact_refs), 5)
        self.assertEqual((await self.repository.get_mission("mis-1")).status.value, "SUCCEEDED")

    async def test_signed_failed_tests_cannot_be_bypassed_by_direct_pass(self):
        await self.seed(statuses=("failed",), report_updates={"conclusion": "failed", "exitCode": 1})
        response = await self.forged_pass()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse(await self.repository.list_evidence("mis-1"))
        self.assertEqual((await self.repository.get_work_unit("wu-1")).status.value, "VERIFYING")
        await self.evaluate()
        self.assertEqual((await self.repository.get_mission("mis-1")).status.value, "FAILED")

    async def test_mc_rechecks_signature_without_trusting_the_verifier_claim(self):
        await self.seed(tamper_signature=True)
        response = await self.forged_pass()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse(await self.repository.list_evidence("mis-1"))
        self.assertEqual((await self.repository.get_mission("mis-1")).status.value, "RUNNING")

    async def test_missing_operator_trust_is_a_failure_not_an_unsigned_fallback(self):
        await self.seed()
        with patch.dict(os.environ, {TRUST_KEYS_FILE_ENV: ""}):
            await self.evaluate()
        evidence = await self.repository.list_evidence("mis-1")
        self.assertEqual([item.verdict.value for item in evidence], ["FAIL"])
        self.assertEqual((await self.repository.get_mission("mis-1")).status.value, "FAILED")

    async def test_signed_old_attempt_cannot_pass(self):
        await self.seed(receipt_updates={"attempt": 2})
        response = await self.forged_pass()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse(await self.repository.list_evidence("mis-1"))

    async def test_physical_tampering_admits_no_evidence(self):
        await self.seed()
        artifact = self.artifacts[0]
        (self.root / "sha256" / artifact.digest[7:]).write_bytes(b"tampered artifact")
        with self.assertRaises(ArtifactIntegrityError):
            await self.evaluate()
        self.assertFalse(await self.repository.list_evidence("mis-1"))

    async def test_valid_signature_with_wrong_candidate_binding_fails(self):
        await self.seed(manifest_updates={"candidateSnapshotDigest": INPUT_DIGEST})
        await self.evaluate()
        self.assertEqual([item.verdict.value for item in await self.repository.list_evidence("mis-1")], ["FAIL"])

    async def test_direct_pass_cannot_omit_a_sixth_failed_report(self):
        await self.seed()
        artifact, bodies = self.additional_failed_report()
        await self.store_artifacts((artifact,), bodies)
        await self.assert_subset_pass_refused()

    async def test_direct_pass_cannot_select_one_of_two_signed_attempt_bundles(self):
        await self.seed()
        second, _, bodies = signed_bundle(statuses=("failed",), report_updates={"conclusion": "failed", "exitCode": 1})
        await self.store_artifacts(second, bodies)
        await self.assert_subset_pass_refused()

    async def test_admission_rechecks_complete_attempt_after_byte_verification(self):
        await self.seed()
        artifact, bodies = self.additional_failed_report()
        verify = self.byte_verifier.verify_all

        async def verify_then_register(artifacts):
            observations = await verify(artifacts)
            await self.store_artifacts((artifact,), bodies)
            return observations

        with patch.object(self.byte_verifier, "verify_all", side_effect=verify_then_register):
            await self.assert_subset_pass_refused()

    async def test_default_integrity_policy_still_accepts_a_registered_subset(self):
        criterion = trusted_criterion()
        criterion["configuration"] = {"evaluator": "artifact-set.v1", "workUnitKinds": ["desktop.task"],
                                      "minimumArtifacts": 1, "requiredArtifactKinds": ["diff"]}
        await self.seed(criterion=criterion)
        artifact, bodies = self.additional_failed_report()
        await self.store_artifacts((artifact,), bodies)
        self.artifacts = tuple(item for item in self.artifacts if item.kind.value == "diff")
        response = await self.forged_pass()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item.verdict.value for item in await self.repository.list_evidence("mis-1")], ["PASS"])
