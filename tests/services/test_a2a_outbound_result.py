from __future__ import annotations

import base64
import copy
import hashlib
import json
import unittest
from typing import Any

from app.services.a2a_outbound_result import (
    A2AOutboundResultError,
    A2AOutboundResultImport,
    A2AOutboundResultImporter,
    parse_a2a_result_bundle,
)
from app.services.a2a_outbound_runner import (
    A2AOutboundClaimedWork,
    A2AOutboundTaskCommand,
    A2ARemoteTaskReference,
)
from app.services.artifact_store_service import PublishedArtifact


def sha256_digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def valid_result_payload(*, content: bytes = b"verified remote result") -> dict[str, Any]:
    digest = sha256_digest(content)
    return {
        "id": "remote-task-1",
        "status": "completed",
        "missionId": "remote-mission-1",
        "workUnitId": "remote-work-unit-1",
        "artifacts": [
            {
                "artifactId": "remote-artifact-1",
                "name": "remote-artifact-1",
                "parts": [
                    {
                        "type": "file",
                        "file": {
                            "name": "remote-artifact-1",
                            "mimeType": "application/json",
                            "bytes": base64.b64encode(content).decode("ascii"),
                        },
                    },
                    {
                        "type": "data",
                        "data": {
                            "kind": "report",
                            "digest": digest,
                            "sizeBytes": len(content),
                        },
                    },
                ],
            }
        ],
        "evidence": [
            {
                "evidenceId": "remote-evidence-1",
                "workUnitId": "remote-work-unit-1",
                "criterionId": "remote-criterion-1",
                "verifier": {"id": "remote-verifier", "version": "1"},
                "verdict": "PASS",
                "artifactRefs": [
                    {"id": "remote-artifact-1", "digest": digest},
                ],
                "summary": "Remote verifier accepted the result.",
                "generatedAt": "2026-08-15T12:00:00+00:00",
                "integrityHash": "sha256:" + "a" * 64,
            }
        ],
    }


def claimed_work() -> A2AOutboundClaimedWork:
    reference = A2ARemoteTaskReference(
        target_agent_url="https://receiver.example.test/a2a",
        source_agent_url="https://sender.example.test",
        workspace_id="workspace-1",
        task_id="remote-task-1",
    )
    return A2AOutboundClaimedWork(
        mission_id="mis-1",
        work_unit_id="wu-1",
        attempt=2,
        lease_id="lease-1",
        timeout_seconds=30,
        command=A2AOutboundTaskCommand(
            reference=reference,
            objective="Build a verified result.",
            required_capabilities=("code_generation",),
        ),
    )


class FakePublisher:
    def __init__(self) -> None:
        self.contents: list[bytes] = []
        self.digest_override: str | None = None

    async def publish_bytes(self, content: bytes) -> PublishedArtifact:
        self.contents.append(content)
        digest = self.digest_override or sha256_digest(content)
        return PublishedArtifact(
            digest=digest,
            size_bytes=len(content),
            content_address=f"local:sha256/{digest.removeprefix('sha256:')}",
        )


class FakeControl:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response_updates: dict[str, Any] = {}

    async def register_artifact(
        self,
        mission_id: str,
        work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "mission_id": mission_id,
                "work_unit_id": work_unit_id,
                **kwargs,
            }
        )
        artifact = kwargs["artifact"]
        response = {
            "id": kwargs["artifact_id"],
            "missionId": mission_id,
            "workUnitId": work_unit_id,
            "attempt": 2,
            "kind": kwargs["kind"],
            "digest": artifact.digest,
            "contentAddress": artifact.content_address,
            "mediaType": kwargs["media_type"],
            "sizeBytes": artifact.size_bytes,
        }
        response.update(self.response_updates)
        return response


class A2AOutboundResultTests(unittest.IsolatedAsyncioTestCase):
    def test_result_import_requires_unique_local_artifacts(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            A2AOutboundResultImport(artifacts=())

    def test_parser_validates_and_decodes_complete_bundle(self) -> None:
        bundle = parse_a2a_result_bundle(
            valid_result_payload(),
            expected_task_id="remote-task-1",
        )

        self.assertEqual(bundle.remote_task_id, "remote-task-1")
        self.assertEqual(len(bundle.artifacts), 1)
        self.assertEqual(bundle.artifacts[0].content, b"verified remote result")
        self.assertEqual(bundle.artifacts[0].kind.value, "report")
        self.assertEqual(bundle.evidence[0]["verdict"], "PASS")

    def test_parser_rejects_invalid_bytes_digest_and_reference_closure(self) -> None:
        cases: list[tuple[str, Any, str]] = [
            (
                "base64",
                lambda payload: payload["artifacts"][0]["parts"][0]["file"].update(
                    {"bytes": "%%%"}
                ),
                "Base64",
            ),
            (
                "size",
                lambda payload: payload["artifacts"][0]["parts"][1]["data"].update(
                    {"sizeBytes": 999}
                ),
                "size",
            ),
            (
                "digest",
                lambda payload: payload["artifacts"][0]["parts"][1]["data"].update(
                    {"digest": "sha256:" + "f" * 64}
                ),
                "digest",
            ),
            (
                "missing reference",
                lambda payload: payload["evidence"][0]["artifactRefs"][0].update(
                    {"id": "missing-artifact"}
                ),
                "unavailable Artifact",
            ),
            (
                "non-pass evidence",
                lambda payload: payload["evidence"][0].update({"verdict": "FAIL"}),
                "schema validation",
            ),
            (
                "work unit mismatch",
                lambda payload: payload.update({"workUnitId": "other-work-unit"}),
                "does not match the result WorkUnit",
            ),
        ]
        for name, mutate, message in cases:
            with self.subTest(name=name):
                payload = copy.deepcopy(valid_result_payload())
                mutate(payload)
                with self.assertRaisesRegex(A2AOutboundResultError, message):
                    parse_a2a_result_bundle(
                        payload,
                        expected_task_id="remote-task-1",
                    )

    async def test_importer_publishes_registers_and_preserves_remote_evidence_as_report(
        self,
    ) -> None:
        control = FakeControl()
        publisher = FakePublisher()
        importer = A2AOutboundResultImporter(
            control,
            publisher,
            runner_id="runner-1",
        )

        result = await importer.import_result(claimed_work(), valid_result_payload())

        self.assertEqual(len(result.artifacts), 2)
        self.assertEqual(len(result.artifact_refs), 2)
        self.assertEqual(len(publisher.contents), 2)
        self.assertEqual(len(control.calls), 2)
        self.assertEqual(control.calls[0]["runner_id"], "runner-1")
        self.assertEqual(control.calls[0]["lease_id"], "lease-1")
        self.assertEqual(control.calls[0]["kind"], "report")
        self.assertEqual(control.calls[1]["kind"], "report")
        self.assertNotEqual(control.calls[0]["artifact_id"], "remote-artifact-1")

        attestation = json.loads(publisher.contents[1])
        self.assertEqual(attestation["authority"], "remote-attestation-only")
        self.assertEqual(attestation["remoteTaskId"], "remote-task-1")
        self.assertEqual(attestation["evidence"][0]["verdict"], "PASS")
        self.assertEqual(
            attestation["artifacts"][0]["localArtifactId"],
            control.calls[0]["artifact_id"],
        )
        self.assertFalse(any("evidence" in call for call in control.calls))

    async def test_importer_rejects_publisher_metadata_before_registration(self) -> None:
        control = FakeControl()
        publisher = FakePublisher()
        publisher.digest_override = "sha256:" + "f" * 64

        with self.assertRaisesRegex(
            A2AOutboundResultError,
            "publisher returned inconsistent metadata",
        ):
            await A2AOutboundResultImporter(
                control,
                publisher,
                runner_id="runner-1",
            ).import_result(claimed_work(), valid_result_payload())

        self.assertEqual(control.calls, [])

    async def test_importer_rejects_inconsistent_registration_response(self) -> None:
        control = FakeControl()
        control.response_updates = {"attempt": 3}
        publisher = FakePublisher()

        with self.assertRaisesRegex(
            A2AOutboundResultError,
            "inconsistent Artifact metadata",
        ):
            await A2AOutboundResultImporter(
                control,
                publisher,
                runner_id="runner-1",
            ).import_result(claimed_work(), valid_result_payload())

        self.assertEqual(len(control.calls), 1)


if __name__ == "__main__":
    unittest.main()
