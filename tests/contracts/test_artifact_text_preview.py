"""Freeze the human preview wire shape against the actual DTO and producer."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator, ValidationError

from app.core.config import ArtifactStoreSettings
from app.schemas.artifact_preview import ArtifactTextPreview
from app.services.artifact_integrity_service import ContentAddressedArtifactByteVerifier
from app.services.artifact_preview_service import PREVIEW_MAX_BYTES, read_artifact_text_preview
from tests.api.artifact_preview_fixture import (
    PREVIEW_PATH,
    PreviewRepository,
    preview_app,
    text_artifact,
)


class ArtifactTextPreviewContractTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parents[2] / "platform/contracts/v1/artifact-text-preview.schema.json"
        cls.schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "sha256").mkdir()
        self.exporter = ContentAddressedArtifactByteVerifier(ArtifactStoreSettings(
            backend="local", local_root=self.root, verify_max_bytes=2 * 1024 * 1024,
        ))

    def register_bytes(self, content, *, media_type="text/plain"):
        artifact = text_artifact(content, media_type=media_type)
        (self.root / "sha256" / artifact.digest.removeprefix("sha256:")).write_bytes(content)
        return artifact

    async def produce(self, content=b"Registered text", *, media_type="text/plain"):
        artifact = self.register_bytes(content, media_type=media_type)
        result = await read_artifact_text_preview(artifact, exporter=self.exporter)
        document = result.to_public_dict()
        self.validator.validate(document)
        return document

    def test_schema_is_the_exact_serialized_dto_with_all_wire_fields_required(self):
        dto = ArtifactTextPreview.model_json_schema(by_alias=True, mode="serialization")
        for key in ("$defs", "properties", "type", "additionalProperties"):
            self.assertEqual(self.schema[key], dto[key])
        self.assertEqual(set(self.schema["required"]), set(dto["properties"]))
        artifact = self.schema["$defs"]["PreviewArtifact"]
        self.assertEqual(set(artifact["properties"]), {
            "id", "missionId", "workUnitId", "attempt", "kind", "mediaType", "sizeBytes", "digest",
        })
        self.assertEqual(set(artifact["required"]), set(artifact["properties"]))
        self.assertEqual(artifact["properties"]["sizeBytes"]["maximum"], PREVIEW_MAX_BYTES)
        self.assertEqual(self.schema["properties"]["text"]["maxLength"], PREVIEW_MAX_BYTES)

    async def test_actual_cas_producer_preserves_utf8_empty_and_boundary_text(self):
        for content, media_type in (
            ("产物 中文 😀\n".encode(), "text/x-python"),
            (b"", "text/plain"),
            (b"x" * PREVIEW_MAX_BYTES, "text/plain"),
            (b'{"source":"<img onerror=untrusted>"}', "application/json"),
        ):
            with self.subTest(size=len(content), media_type=media_type):
                document = await self.produce(content, media_type=media_type)
                self.assertEqual(document["text"], content.decode("utf-8"))
                self.assertEqual(document["artifact"]["sizeBytes"], len(content))
                self.assertEqual(document["integrity"], "verified")
                self.assertNotIn("verdict", document)

    async def test_actual_http_serialization_matches_contract_and_excludes_storage_metadata(self):
        for content in ("print('产物 中文')\n".encode(), b""):
            with self.subTest(size=len(content)):
                artifact = self.register_bytes(content, media_type="text/x-python")
                app = preview_app(PreviewRepository(artifact), self.exporter)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://mission-control.test",
                ) as client:
                    response = await client.get(PREVIEW_PATH)
                self.assertEqual(response.status_code, 200, response.text)
                self.validator.validate(response.json())
                self.assertEqual(response.json()["text"], content.decode())
                self.assertNotIn("contentAddress", response.text)
                self.assertNotIn("sensitivity", response.text)

    async def test_missing_top_level_or_artifact_fields_are_rejected(self):
        document = await self.produce()
        for location in ("top", "artifact"):
            fields = document if location == "top" else document["artifact"]
            for field in fields:
                changed = copy.deepcopy(document)
                target = changed if location == "top" else changed["artifact"]
                target.pop(field)
                with self.subTest(location=location, field=field), self.assertRaises(ValidationError):
                    self.validator.validate(changed)

    async def test_unknown_fields_fake_verdicts_wrong_types_and_limits_are_rejected(self):
        document = await self.produce()
        top_changes = (
            {"schemaVersion": 2}, {"schemaVersion": True}, {"encoding": "base64"},
            {"integrity": "PASS"}, {"integrity": "unverified"}, {"verdict": "PASS"},
            {"evidence": {}}, {"status": "SUCCEEDED"}, {"text": None}, {"text": 1},
            {"text": "x" * (PREVIEW_MAX_BYTES + 1)}, {"artifact": []},
        )
        artifact_changes = (
            {"id": ""}, {"id": "bad/id"}, {"missionId": None}, {"workUnitId": ""},
            {"attempt": 0}, {"attempt": True}, {"kind": "arbitrary"}, {"mediaType": ""},
            {"sizeBytes": -1}, {"sizeBytes": True}, {"sizeBytes": PREVIEW_MAX_BYTES + 1},
            {"digest": "sha256:incorrect"}, {"contentAddress": "local:sha256/private"},
            {"sensitivity": "internal"}, {"createdBy": {"type": "human", "id": "private"}},
            {"verdict": "PASS"},
        )
        for location, changes in (("top", top_changes), ("artifact", artifact_changes)):
            for mutation in changes:
                changed = copy.deepcopy(document)
                target = changed if location == "top" else changed["artifact"]
                target.update(mutation)
                with self.subTest(location=location, mutation=mutation), self.assertRaises(ValidationError):
                    self.validator.validate(changed)
