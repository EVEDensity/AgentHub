from __future__ import annotations

import base64
import json

from app.domain import (
    Artifact,
    ArtifactSensitivity,
    Evidence,
    EvidenceVerdict,
    WorkUnit,
)
from app.repositories import MissionRepository
from app.services.artifact_integrity_service import (
    ArtifactByteExporter,
    ArtifactBytesUnavailableError,
)

_MAX_ARTIFACTS = 20
_MAX_EVIDENCE = 20
_MAX_RAW_BYTES = 512 * 1024
_MAX_PROJECTION_BYTES = 900 * 1024
_PAGE_SIZE = 200
_EXPORTABLE_SENSITIVITY = {
    ArtifactSensitivity.PUBLIC,
    ArtifactSensitivity.INTERNAL,
}


class A2AResultBundlePolicyError(ValueError):
    pass


class A2AResultBundleTooLargeError(A2AResultBundlePolicyError):
    pass


class A2AResultBundleService:
    """Builds an all-or-nothing peer result projection from Mission truth."""

    def __init__(
        self,
        repository: MissionRepository,
        artifact_byte_exporter: ArtifactByteExporter | None,
    ) -> None:
        self._repository = repository
        self._artifact_byte_exporter = artifact_byte_exporter

    async def export(self, mission_id: str, work_unit: WorkUnit) -> dict:
        evidence = await self._result_evidence(mission_id, work_unit)
        artifacts = await self._result_artifacts(mission_id, work_unit, evidence)
        bundle = {
            "artifacts": await self._export_artifacts(artifacts),
            "evidence": [self._evidence_projection(item) for item in evidence],
        }
        projection_size = len(
            json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode()
        )
        if projection_size > _MAX_PROJECTION_BYTES:
            raise A2AResultBundleTooLargeError(
                "A2A result bundle exceeds the encoded response limit"
            )
        return bundle

    async def _result_evidence(
        self,
        mission_id: str,
        work_unit: WorkUnit,
    ) -> list[Evidence]:
        selected: list[Evidence] = []
        offset = 0
        while True:
            page = await self._repository.list_evidence(
                mission_id,
                limit=_PAGE_SIZE,
                offset=offset,
            )
            for item in page:
                if (
                    item.work_unit_id != work_unit.id
                    or item.verdict != EvidenceVerdict.PASS
                ):
                    continue
                selected.append(item)
                if len(selected) > _MAX_EVIDENCE:
                    raise A2AResultBundleTooLargeError(
                        "A2A result bundle exceeds the Evidence count limit"
                    )
            if len(page) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        if not selected:
            raise A2AResultBundlePolicyError(
                "completed inbound Mission has no PASS Evidence"
            )
        return selected

    async def _result_artifacts(
        self,
        mission_id: str,
        work_unit: WorkUnit,
        evidence: list[Evidence],
    ) -> list[Artifact]:
        referenced: dict[str, str] = {}
        for item in evidence:
            for artifact_ref in item.artifact_refs:
                existing_digest = referenced.get(artifact_ref.id)
                if (
                    existing_digest is not None
                    and existing_digest.lower() != artifact_ref.digest.lower()
                ):
                    raise A2AResultBundlePolicyError(
                        "PASS Evidence contains conflicting Artifact references"
                    )
                referenced[artifact_ref.id] = artifact_ref.digest
        if not referenced:
            raise A2AResultBundlePolicyError(
                "PASS Evidence does not reference an Artifact"
            )
        if len(referenced) > _MAX_ARTIFACTS:
            raise A2AResultBundleTooLargeError(
                "A2A result bundle exceeds the Artifact count limit"
            )

        selected: dict[str, Artifact] = {}
        offset = 0
        while len(selected) < len(referenced):
            page = await self._repository.list_artifacts(
                mission_id,
                limit=_PAGE_SIZE,
                offset=offset,
            )
            for artifact in page:
                if (
                    artifact.id in referenced
                    and artifact.work_unit_id == work_unit.id
                    and artifact.attempt == work_unit.attempt
                ):
                    selected[artifact.id] = artifact
            if len(page) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE

        if set(selected) != set(referenced):
            raise A2AResultBundlePolicyError(
                "PASS Evidence references an unavailable current-attempt Artifact"
            )
        artifacts = [selected[artifact_id] for artifact_id in sorted(selected)]
        for artifact in artifacts:
            if artifact.digest.lower() != referenced[artifact.id].lower():
                raise A2AResultBundlePolicyError(
                    "PASS Evidence Artifact digest does not match Mission Control"
                )
            if artifact.sensitivity not in _EXPORTABLE_SENSITIVITY:
                raise A2AResultBundlePolicyError(
                    "A2A result bundle contains a non-exportable Artifact"
                )
        if sum(artifact.size_bytes for artifact in artifacts) > _MAX_RAW_BYTES:
            raise A2AResultBundleTooLargeError(
                "A2A result bundle exceeds the byte limit"
            )
        return artifacts

    async def _export_artifacts(self, artifacts: list[Artifact]) -> list[dict]:
        if self._artifact_byte_exporter is None:
            raise ArtifactBytesUnavailableError(
                "Artifact byte export is not configured"
            )
        exported: list[dict] = []
        remaining_bytes = _MAX_RAW_BYTES
        for artifact in artifacts:
            content = await self._artifact_byte_exporter.read_verified(
                artifact,
                max_bytes=remaining_bytes,
            )
            remaining_bytes -= len(content)
            exported.append(
                {
                    "artifactId": artifact.id,
                    "name": artifact.id,
                    "parts": [
                        {
                            "type": "file",
                            "file": {
                                "name": artifact.id,
                                "mimeType": artifact.media_type,
                                "bytes": base64.b64encode(content).decode("ascii"),
                            },
                        },
                        {
                            "type": "data",
                            "data": {
                                "kind": artifact.kind.value,
                                "digest": artifact.digest,
                                "sizeBytes": artifact.size_bytes,
                            },
                        },
                    ],
                }
            )
        return exported

    @staticmethod
    def _evidence_projection(evidence: Evidence) -> dict:
        verifier = {
            "id": evidence.verifier.id,
            "version": evidence.verifier.version,
        }
        if evidence.verifier.configuration_digest is not None:
            verifier["configurationDigest"] = evidence.verifier.configuration_digest
        return {
            "evidenceId": evidence.id,
            "workUnitId": evidence.work_unit_id,
            "criterionId": evidence.criterion_id,
            "verifier": verifier,
            "verdict": evidence.verdict.value,
            "artifactRefs": [
                {"id": ref.id, "digest": ref.digest} for ref in evidence.artifact_refs
            ],
            "summary": evidence.summary,
            "generatedAt": evidence.generated_at.isoformat(),
            "integrityHash": evidence.integrity_hash,
        }
