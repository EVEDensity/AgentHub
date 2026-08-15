from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from app.core.config import ArtifactStoreSettings
from app.domain import ArtifactRef
from app.services.artifact_integrity_service import (
    ArtifactByteVerification,
    ContentAddressedArtifactByteVerifier,
)
from app.services.verifier_service import (
    ControlledVerifier,
    MissionControlVerifierClient,
    VerificationPolicyUnavailableError,
    VerificationSubmission,
    VerifierControlError,
    VerifierPollStatus,
    VerifierProtocolError,
)
from tests.domain.factories import (
    build_evidence,
    build_mission,
    build_work_unit,
)

DIGEST = "sha256:" + "a" * 64


def discovery_payload(
    *,
    policy: dict[str, Any] | None = None,
    digest: str = DIGEST,
    content_address: str = "local:sha256/" + "a" * 64,
    size_bytes: int = 128,
) -> dict[str, Any]:
    return {
        "discoveryStatus": "ready",
        "verificationContext": {
            "version": 2,
            "mission": {
                "id": "mis-1",
                "title": "Mission",
                "objective": "Produce a verified Artifact.",
            },
            "contract": {
                "id": "contract-1",
                "version": 1,
                "acceptanceCriteria": [
                    {
                        "id": "tests",
                        "kind": "test",
                        "description": "Artifacts exist",
                        "required": True,
                        "configuration": {
                            "evaluator": "artifact-set.v1",
                            "workUnitKinds": ["code_change"],
                            "minimumArtifacts": 1,
                            "requiredArtifactKinds": ["diff"],
                        },
                    }
                ],
            },
            "workUnit": {
                "id": "wu-1",
                "kind": "code_change",
                "inputRefs": [],
                "expectedOutputs": [{"kind": "diff", "required": True}],
                "status": "VERIFYING",
                "attempt": 1,
            },
            "artifacts": [
                {
                    "id": "artifact-1",
                    "attempt": 1,
                    "kind": "diff",
                    "digest": digest,
                    "contentAddress": content_address,
                    "mediaType": "text/x-diff",
                    "sizeBytes": size_bytes,
                    "sensitivity": "internal",
                }
            ],
            "evaluationPolicy": policy
            or {
                "status": "ready",
                "criterionId": "tests",
                "evaluator": "artifact-set.v1",
                "configurationDigest": "sha256:" + "c" * 64,
                "parameters": {
                    "minimumArtifacts": 1,
                    "requiredArtifactKinds": ["diff"],
                },
            },
        },
    }


def admission_payload(submission: VerificationSubmission) -> dict[str, Any]:
    return {
        "evidence": build_evidence(
            verifier={
                "id": submission.verifier_id,
                "version": submission.verifier_version,
                "configuration_digest": submission.configuration_digest,
            },
            verdict=submission.verdict,
            artifact_refs=submission.artifact_refs,
            summary=submission.summary,
        ).to_public_dict(),
        "workUnit": build_work_unit(
            status="SUCCEEDED",
            attempt=1,
        ).to_public_dict(),
        "mission": build_mission(status="RUNNING").to_public_dict(),
    }


class FakeControl:
    def __init__(self, discovery: dict[str, Any]) -> None:
        self.discovery = discovery
        self.discovery_calls: list[str] = []
        self.submissions: list[tuple[str, str, VerificationSubmission]] = []

    async def discover_verification_work(
        self,
        workspace_id: str,
    ) -> dict[str, Any]:
        self.discovery_calls.append(workspace_id)
        return self.discovery

    async def submit_verification(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        submission: VerificationSubmission,
    ) -> dict[str, Any]:
        self.submissions.append((mission_id, work_unit_id, submission))
        return admission_payload(submission)


class RecordingByteVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def verify_all(
        self,
        artifacts: tuple[Any, ...],
    ) -> list[ArtifactByteVerification]:
        self.calls.append(artifacts)
        return [
            ArtifactByteVerification(
                artifact_id=artifact.id,
                digest=artifact.digest,
                size_bytes=artifact.size_bytes,
            )
            for artifact in artifacts
        ]


class BlockingByteVerifier:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def verify_all(self, artifacts: tuple[Any, ...]) -> list[Any]:
        del artifacts
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class ControlledVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_discovery_performs_no_byte_io_or_submission(self) -> None:
        control = FakeControl({"discoveryStatus": "idle", "verificationContext": None})
        byte_verifier = RecordingByteVerifier()
        verifier = ControlledVerifier(
            control,
            byte_verifier=byte_verifier,
            verifier_id="verifier-1",
            verifier_version="artifact-set-runtime.v1",
        )

        result = await verifier.discover_and_verify("workspace-1")

        self.assertEqual(result.status, VerifierPollStatus.IDLE)
        self.assertEqual(control.discovery_calls, ["workspace-1"])
        self.assertEqual(byte_verifier.calls, [])
        self.assertEqual(control.submissions, [])

    async def test_ready_policy_submits_only_controlled_pass(self) -> None:
        control = FakeControl(discovery_payload())
        byte_verifier = RecordingByteVerifier()
        verifier = ControlledVerifier(
            control,
            byte_verifier=byte_verifier,
            verifier_id="verifier-1",
            verifier_version="artifact-set-runtime.v1",
        )

        result = await verifier.discover_and_verify("workspace-1")

        self.assertEqual(result.status, VerifierPollStatus.VERIFIED)
        self.assertEqual(result.evidence_id, "evd-1")
        self.assertEqual(len(byte_verifier.calls), 1)
        artifact = byte_verifier.calls[0][0]
        self.assertEqual(artifact.mission_id, "mis-1")
        self.assertEqual(artifact.work_unit_id, "wu-1")
        self.assertEqual(artifact.attempt, 1)
        mission_id, work_unit_id, submission = control.submissions[0]
        self.assertEqual((mission_id, work_unit_id), ("mis-1", "wu-1"))
        self.assertEqual(submission.verdict.value, "PASS")
        self.assertEqual(submission.criterion_id, "tests")
        self.assertEqual(submission.configuration_digest, "sha256:" + "c" * 64)
        self.assertEqual(
            submission.artifact_refs,
            (ArtifactRef(id="artifact-1", digest=DIGEST),),
        )
        self.assertNotIn("Mission", submission.summary)

    async def test_inconclusive_policy_never_invents_evidence_criterion(self) -> None:
        control = FakeControl(
            discovery_payload(
                policy={
                    "status": "inconclusive",
                    "reasonCode": "no_applicable_policy",
                }
            )
        )
        byte_verifier = RecordingByteVerifier()
        verifier = ControlledVerifier(
            control,
            byte_verifier=byte_verifier,
            verifier_id="verifier-1",
            verifier_version="artifact-set-runtime.v1",
        )

        with self.assertRaisesRegex(
            VerificationPolicyUnavailableError,
            "no_applicable_policy",
        ):
            await verifier.discover_and_verify("workspace-1")

        self.assertEqual(byte_verifier.calls, [])
        self.assertEqual(control.submissions, [])

    async def test_malformed_or_mismatched_responses_fail_closed(self) -> None:
        malformed_control = FakeControl(
            {"discoveryStatus": "ready", "verificationContext": None}
        )
        verifier = ControlledVerifier(
            malformed_control,
            byte_verifier=RecordingByteVerifier(),
            verifier_id="verifier-1",
            verifier_version="artifact-set-runtime.v1",
        )
        with self.assertRaises(VerifierProtocolError):
            await verifier.discover_and_verify("workspace-1")

        class MismatchedControl(FakeControl):
            async def submit_verification(
                self,
                mission_id: str,
                work_unit_id: str,
                *,
                submission: VerificationSubmission,
            ) -> dict[str, Any]:
                payload = admission_payload(submission)
                payload["evidence"]["criterionId"] = "another-criterion"
                return payload

        verifier = ControlledVerifier(
            MismatchedControl(discovery_payload()),
            byte_verifier=RecordingByteVerifier(),
            verifier_id="verifier-1",
            verifier_version="artifact-set-runtime.v1",
        )
        with self.assertRaisesRegex(VerifierProtocolError, "does not match"):
            await verifier.discover_and_verify("workspace-1")

    async def test_task_cancellation_propagates_to_byte_verifier(self) -> None:
        byte_verifier = BlockingByteVerifier()
        verifier = ControlledVerifier(
            FakeControl(discovery_payload()),
            byte_verifier=byte_verifier,
            verifier_id="verifier-1",
            verifier_version="artifact-set-runtime.v1",
        )
        task = asyncio.create_task(verifier.discover_and_verify("workspace-1"))
        await asyncio.wait_for(byte_verifier.started.wait(), timeout=1)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(byte_verifier.cancelled)

    async def test_real_local_bytes_are_verified_before_submission(self) -> None:
        content = b"independently verified Artifact bytes"
        digest_hex = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as temporary_directory:
            local_root = Path(temporary_directory)
            artifact_path = local_root / "sha256" / digest_hex
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_bytes(content)
            control = FakeControl(
                discovery_payload(
                    digest=f"sha256:{digest_hex}",
                    content_address=f"local:sha256/{digest_hex}",
                    size_bytes=len(content),
                )
            )
            verifier = ControlledVerifier(
                control,
                byte_verifier=ContentAddressedArtifactByteVerifier(
                    ArtifactStoreSettings(
                        local_root=local_root,
                        verify_max_bytes=1024,
                    )
                ),
                verifier_id="verifier-1",
                verifier_version="artifact-set-runtime.v1",
            )

            result = await verifier.discover_and_verify("workspace-1")

        self.assertEqual(result.status, VerifierPollStatus.VERIFIED)
        self.assertEqual(len(control.submissions), 1)


class MissionControlVerifierClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_uses_narrow_routes_and_verifier_authorization(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("/discover"):
                return httpx.Response(
                    200,
                    json={"discoveryStatus": "idle", "verificationContext": None},
                )
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = MissionControlVerifierClient(
                "http://mission-control/",
                access_token="verifier-token",
                http_client=http_client,
            )
            await client.discover_verification_work("workspace-1")
            await client.submit_verification(
                "mis-1",
                "wu-1",
                submission=VerificationSubmission(
                    criterion_id="tests",
                    verifier_id="verifier-1",
                    verifier_version="v1",
                    configuration_digest="sha256:" + "c" * 64,
                    verdict=build_evidence().verdict,
                    artifact_refs=(ArtifactRef(id="artifact-1", digest=DIGEST),),
                    summary="Verified one Artifact.",
                ),
            )

        self.assertEqual(
            [request.url.path for request in requests],
            [
                "/api/v1/missions/verification-work-items/discover",
                "/api/v1/missions/mis-1/work-units/wu-1/verify",
            ],
        )
        self.assertEqual(
            [request.headers["Authorization"] for request in requests],
            ["Bearer verifier-token", "Bearer verifier-token"],
        )
        self.assertEqual(requests[0].read(), b'{"workspaceId":"workspace-1"}')
        self.assertNotIn(b"integrityHash", requests[1].read())

    async def test_client_maps_rejection_without_returning_remote_payload(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(409, json={"detail": "Evidence race lost"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = MissionControlVerifierClient(
                "http://mission-control",
                http_client=http_client,
            )
            with self.assertRaisesRegex(VerifierControlError, "Evidence race lost"):
                await client.discover_verification_work("workspace-1")


if __name__ == "__main__":
    unittest.main()
