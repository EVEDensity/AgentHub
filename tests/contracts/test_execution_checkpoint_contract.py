from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from tests.domain.factories import build_execution_checkpoint


class ExecutionCheckpointContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        directory = Path(__file__).parents[2] / "platform/contracts/v1"
        documents = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in directory.glob("*.schema.json")
        ]
        registry = Registry().with_resources(
            (document["$id"], Resource.from_contents(document))
            for document in documents
        )
        schema = json.loads(
            (directory / "execution-checkpoint.schema.json").read_text(encoding="utf-8")
        )
        cls.validator = Draft202012Validator(schema, registry=registry)

    def test_legacy_domain_projection_remains_valid_without_resume_fields(self) -> None:
        self.validator.validate(build_execution_checkpoint().to_public_dict())

    def test_current_domain_projection_preserves_resume_metadata(self) -> None:
        checkpoint = build_execution_checkpoint(
            phase="harness.tool.started",
            resume_protocol_version=1,
            next_action={
                "toolName": "file_write",
                "callId": "call-1",
                "argumentsDigest": "a" * 64,
            },
            idempotency_key="mis-1/wu-1/1/file_write/digest",
            workspace_revision="revision-1",
            context_manifest_digest="manifest-1",
        )
        document = checkpoint.to_public_dict()
        self.assertEqual(document["resumeProtocolVersion"], 1)
        self.assertEqual(document["nextAction"]["callId"], "call-1")
        self.validator.validate(document)

    def test_completed_projection_can_keep_fingerprints_without_pending_action(
        self,
    ) -> None:
        checkpoint = build_execution_checkpoint(
            phase="harness.execution.completed",
            terminal=True,
            workspace_revision="revision-1",
            context_manifest_digest="manifest-1",
        )
        self.validator.validate(checkpoint.to_public_dict())

    def test_resume_fields_obey_domain_bounds(self) -> None:
        base = build_execution_checkpoint().to_public_dict()
        invalid = (
            {"resumeProtocolVersion": 0},
            {"resumeProtocolVersion": 11},
            {"resumeProtocolVersion": "1"},
            {"workspaceRevision": ""},
            {"workspaceRevision": "x" * 256},
            {"contextManifestDigest": ""},
            {"idempotencyKey": "unscoped"},
            {"idempotencyKey": "/" + "x" * 512},
            {"resumeProtocolVersion": 1, "nextAction": {}},
            {"nextAction": {"toolName": "file_write", "callId": "call-1"}},
        )
        for fields in invalid:
            with self.subTest(fields=fields):
                self.assertTrue(list(self.validator.iter_errors({**base, **fields})))
