from __future__ import annotations

import hashlib
import unittest

from app.services.a2a_result_bundle_service import (
    A2AResultBundlePolicyError,
    A2AResultBundleService,
    A2AResultBundleTooLargeError,
)
from tests.api.test_missions_api import FakeMissionRepository
from tests.domain.factories import build_artifact, build_evidence, build_work_unit


class FakeArtifactByteExporter:
    def __init__(self) -> None:
        self.contents: dict[str, bytes] = {}
        self.calls: list[tuple[str, int]] = []

    async def read_verified(self, artifact, *, max_bytes: int) -> bytes:
        self.calls.append((artifact.id, max_bytes))
        return self.contents[artifact.id]


class A2AResultBundleServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = FakeMissionRepository()
        self.exporter = FakeArtifactByteExporter()
        self.service = A2AResultBundleService(self.repository, self.exporter)
        self.work_unit = build_work_unit(
            id="work-unit-inbound",
            mission_id="mission-inbound",
            kind="a2a.inbound",
            status="SUCCEEDED",
            attempt=1,
        )

    def add_result(
        self,
        *,
        content: bytes = b"verified result",
        artifact_attempt: int = 1,
        evidence_digest: str | None = None,
        verdict: str = "PASS",
    ) -> None:
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        artifact = build_artifact(
            id="artifact-result",
            mission_id=self.work_unit.mission_id,
            work_unit_id=self.work_unit.id,
            attempt=artifact_attempt,
            digest=digest,
            content_address=f"local:sha256/{digest.removeprefix('sha256:')}",
            size_bytes=len(content),
        )
        evidence = build_evidence(
            id="evidence-result",
            mission_id=self.work_unit.mission_id,
            work_unit_id=self.work_unit.id,
            verdict=verdict,
            artifact_refs=[
                {"id": artifact.id, "digest": evidence_digest or artifact.digest}
            ],
        )
        self.repository.artifacts.append(artifact)
        self.repository.evidence.append(evidence)
        self.exporter.contents[artifact.id] = content

    async def test_rejects_artifact_from_an_expired_attempt(self) -> None:
        self.add_result(artifact_attempt=2)

        with self.assertRaisesRegex(
            A2AResultBundlePolicyError,
            "current-attempt Artifact",
        ):
            await self.service.export(self.work_unit.mission_id, self.work_unit)

        self.assertEqual(self.exporter.calls, [])

    async def test_rejects_evidence_digest_mismatch(self) -> None:
        self.add_result(evidence_digest="sha256:" + "f" * 64)

        with self.assertRaisesRegex(
            A2AResultBundlePolicyError,
            "digest does not match",
        ):
            await self.service.export(self.work_unit.mission_id, self.work_unit)

        self.assertEqual(self.exporter.calls, [])

    async def test_rejects_completion_without_pass_evidence(self) -> None:
        self.add_result(verdict="FAIL")

        with self.assertRaisesRegex(A2AResultBundlePolicyError, "no PASS Evidence"):
            await self.service.export(self.work_unit.mission_id, self.work_unit)

        self.assertEqual(self.exporter.calls, [])

    async def test_rejects_declared_raw_bytes_over_exchange_limit(self) -> None:
        self.add_result(content=b"x")
        self.repository.artifacts[0] = self.repository.artifacts[0].model_copy(
            update={"size_bytes": 512 * 1024 + 1}
        )

        with self.assertRaisesRegex(A2AResultBundleTooLargeError, "byte limit"):
            await self.service.export(self.work_unit.mission_id, self.work_unit)

        self.assertEqual(self.exporter.calls, [])

    async def test_rejects_encoded_projection_before_gateway_limit(self) -> None:
        content = b"x" * (300 * 1024)
        self.add_result(content=content)
        original = self.repository.evidence[0]
        self.repository.evidence = [
            original.model_copy(
                update={
                    "id": f"evidence-{index}",
                    "criterion_id": f"criterion-{index}",
                    "summary": "界" * 10000,
                }
            )
            for index in range(20)
        ]

        with self.assertRaisesRegex(
            A2AResultBundleTooLargeError,
            "encoded response limit",
        ):
            await self.service.export(self.work_unit.mission_id, self.work_unit)

        self.assertEqual(self.exporter.calls, [("artifact-result", 512 * 1024)])


if __name__ == "__main__":
    unittest.main()
