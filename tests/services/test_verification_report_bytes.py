import tempfile
import unittest
from pathlib import Path

from app.core.config import ArtifactStoreSettings
from app.services.artifact_integrity_service import ContentAddressedArtifactByteVerifier
from app.services.verification_report_models import REPORT_MAX_BYTES
from app.services.verification_evaluator_service import StrictVerificationEvaluator
from tests.services.verification_report_fixtures import make_test_report, report_artifact
from tests.services.test_verification_report_evaluator import plan


class VerificationReportByteTests(unittest.IsolatedAsyncioTestCase):
    async def observed(self, directory, document, kind):
        artifact, expected = report_artifact(document, kind)
        root = Path(directory)
        path = root / "sha256" / artifact.digest.removeprefix("sha256:")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(expected.report_content)
        verifier = ContentAddressedArtifactByteVerifier(ArtifactStoreSettings(
            backend="local", local_root=root, verify_max_bytes=2 * REPORT_MAX_BYTES,
        ))
        return artifact, await verifier.verify(artifact)

    async def test_only_bounded_report_kinds_collect_internal_content(self):
        with tempfile.TemporaryDirectory() as directory:
            for kind in ("report", "test-result", "diff", "log", "file", "build"):
                artifact, observation = await self.observed(directory, make_test_report(), kind)
                self.assertEqual(observation.report_content is not None, kind in {"report", "test-result"})
                self.assertNotIn("cases", repr(observation))
                self.assertEqual(observation.digest, artifact.digest)
                self.assertEqual(observation.size_bytes, artifact.size_bytes)

    async def test_oversized_reports_are_verified_without_collection_and_fail_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, observation = await self.observed(directory, b" " * (REPORT_MAX_BYTES + 1), "test-result")
            self.assertIsNone(observation.report_content)
            self.assertEqual(observation.size_bytes, REPORT_MAX_BYTES + 1)
            result = StrictVerificationEvaluator().evaluate(plan(), (artifact,), (observation,))
            self.assertEqual(result.verdict.value, "FAIL")
