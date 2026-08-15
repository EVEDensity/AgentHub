from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.domain import ArtifactKind
from app.services.a2a_outbound_runner import A2AOutboundClaimedWork
from app.services.artifact_store_service import ArtifactPublisher, PublishedArtifact
from app.services.runner_service import MissionControlRunnerPort, RunnerExecutionError

_MAX_ARTIFACTS = 20
_MAX_EVIDENCE = 20
_MAX_RAW_BYTES = 512 * 1024
_MAX_PROJECTION_BYTES = 900 * 1024
_MAX_BASE64_CHARS = ((_MAX_RAW_BYTES + 2) // 3) * 4
_MAX_ATTESTATION_BYTES = 512 * 1024
_DIGEST_PATTERN = r"^sha256:[a-fA-F0-9]{64}$"
_ATTESTATION_MEDIA_TYPE = "application/vnd.agenthub.a2a-attestation+json"

_BoundedId = Annotated[str, Field(min_length=1, max_length=255)]
_Digest = Annotated[str, Field(pattern=_DIGEST_PATTERN)]


class A2AOutboundResultError(RunnerExecutionError):
    """Raised when a remote result cannot become honest local Artifacts."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _RemoteFile(_StrictModel):
    name: _BoundedId
    mime_type: Annotated[str, Field(alias="mimeType", min_length=1, max_length=255)]
    encoded_bytes: Annotated[
        str,
        Field(alias="bytes", max_length=_MAX_BASE64_CHARS),
    ]


class _RemoteFilePart(_StrictModel):
    type: Literal["file"]
    file: _RemoteFile


class _RemoteArtifactData(_StrictModel):
    kind: _BoundedId
    digest: _Digest
    size_bytes: Annotated[int, Field(alias="sizeBytes", ge=0, le=_MAX_RAW_BYTES)]


class _RemoteDataPart(_StrictModel):
    type: Literal["data"]
    data: _RemoteArtifactData


class _RemoteArtifact(_StrictModel):
    artifact_id: _BoundedId = Field(alias="artifactId")
    name: _BoundedId
    parts: Annotated[
        list[_RemoteFilePart | _RemoteDataPart],
        Field(min_length=2, max_length=2),
    ]

    @model_validator(mode="after")
    def validate_parts(self) -> _RemoteArtifact:
        file_parts = [part for part in self.parts if isinstance(part, _RemoteFilePart)]
        data_parts = [part for part in self.parts if isinstance(part, _RemoteDataPart)]
        if len(file_parts) != 1 or len(data_parts) != 1:
            raise ValueError("remote Artifact requires one file and one data part")
        if self.name != self.artifact_id or file_parts[0].file.name != self.artifact_id:
            raise ValueError("remote Artifact names must match its id")
        _validate_media_type(file_parts[0].file.mime_type)
        return self

    @property
    def file_part(self) -> _RemoteFilePart:
        return next(part for part in self.parts if isinstance(part, _RemoteFilePart))

    @property
    def data_part(self) -> _RemoteDataPart:
        return next(part for part in self.parts if isinstance(part, _RemoteDataPart))


class _RemoteArtifactRef(_StrictModel):
    id: _BoundedId
    digest: _Digest


class _RemoteVerifier(_StrictModel):
    id: _BoundedId
    version: _BoundedId
    configuration_digest: _Digest | None = Field(
        default=None,
        alias="configurationDigest",
    )


class _RemoteEvidence(_StrictModel):
    evidence_id: _BoundedId = Field(alias="evidenceId")
    work_unit_id: _BoundedId = Field(alias="workUnitId")
    criterion_id: _BoundedId = Field(alias="criterionId")
    verifier: _RemoteVerifier
    verdict: Literal["PASS"]
    artifact_refs: Annotated[
        list[_RemoteArtifactRef],
        Field(alias="artifactRefs", min_length=1, max_length=_MAX_ARTIFACTS),
    ]
    summary: Annotated[str, Field(min_length=1, max_length=10_000)]
    generated_at: Annotated[str, Field(alias="generatedAt", min_length=1, max_length=64)]
    integrity_hash: _Digest = Field(alias="integrityHash")

    @model_validator(mode="after")
    def validate_evidence(self) -> _RemoteEvidence:
        reference_ids = [reference.id for reference in self.artifact_refs]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("remote Evidence Artifact references must be unique")
        try:
            generated_at = datetime.fromisoformat(self.generated_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("remote Evidence generatedAt must be ISO-8601") from exc
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("remote Evidence generatedAt must include a timezone")
        return self


class _RemoteCompletedTask(_StrictModel):
    id: _BoundedId
    status: Literal["completed"]
    mission_id: _BoundedId | None = Field(default=None, alias="missionId")
    work_unit_id: _BoundedId | None = Field(default=None, alias="workUnitId")
    artifacts: Annotated[
        list[_RemoteArtifact],
        Field(min_length=1, max_length=_MAX_ARTIFACTS),
    ]
    evidence: Annotated[
        list[_RemoteEvidence],
        Field(min_length=1, max_length=_MAX_EVIDENCE),
    ]


@dataclass(frozen=True, slots=True)
class A2ARemoteArtifactContent:
    remote_artifact_id: str
    kind: ArtifactKind
    media_type: str
    digest: str
    content: bytes


@dataclass(frozen=True, slots=True)
class A2AValidatedResultBundle:
    remote_task_id: str
    artifacts: tuple[A2ARemoteArtifactContent, ...]
    evidence: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class A2AImportedArtifact:
    artifact_id: str
    remote_artifact_id: str | None
    kind: ArtifactKind
    media_type: str
    published: PublishedArtifact


@dataclass(frozen=True, slots=True)
class A2AOutboundResultImport:
    artifacts: tuple[A2AImportedArtifact, ...]

    def __post_init__(self) -> None:
        if not self.artifacts:
            raise ValueError("result import requires at least one local Artifact")
        if any(not isinstance(item, A2AImportedArtifact) for item in self.artifacts):
            raise TypeError("result import contains an invalid local Artifact")
        artifact_ids = [item.artifact_id for item in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("result import local Artifact ids must be unique")

    @property
    def artifact_refs(self) -> list[dict[str, str]]:
        return [
            {"id": artifact.artifact_id, "digest": artifact.published.digest}
            for artifact in self.artifacts
        ]


class A2AOutboundResultImporterPort(Protocol):
    async def import_result(
        self,
        work: A2AOutboundClaimedWork,
        payload: Mapping[str, Any],
    ) -> A2AOutboundResultImport: ...


class A2AOutboundResultImporter:
    """Validate, publish, and register a completed remote result bundle."""

    def __init__(
        self,
        control: MissionControlRunnerPort,
        publisher: ArtifactPublisher,
        *,
        runner_id: str,
    ) -> None:
        if not isinstance(runner_id, str) or not runner_id.strip():
            raise ValueError("runner_id must be non-empty")
        self._control = control
        self._publisher = publisher
        self._runner_id = runner_id

    async def import_result(
        self,
        work: A2AOutboundClaimedWork,
        payload: Mapping[str, Any],
    ) -> A2AOutboundResultImport:
        if not isinstance(work, A2AOutboundClaimedWork):
            raise TypeError("work must be an A2AOutboundClaimedWork")
        bundle = parse_a2a_result_bundle(
            payload,
            expected_task_id=work.command.reference.task_id,
        )

        imported: list[A2AImportedArtifact] = []
        for artifact in bundle.artifacts:
            published = await self._publisher.publish_bytes(artifact.content)
            _assert_published_artifact(
                published,
                expected_digest=artifact.digest,
                expected_size=len(artifact.content),
            )
            imported.append(
                A2AImportedArtifact(
                    artifact_id=_local_artifact_id(
                        work,
                        remote_id=artifact.remote_artifact_id,
                        digest=artifact.digest,
                    ),
                    remote_artifact_id=artifact.remote_artifact_id,
                    kind=artifact.kind,
                    media_type=artifact.media_type,
                    published=published,
                )
            )

        attestation_bytes = _attestation_bytes(work, bundle, imported)
        attestation = await self._publisher.publish_bytes(attestation_bytes)
        attestation_digest = _sha256_digest(attestation_bytes)
        _assert_published_artifact(
            attestation,
            expected_digest=attestation_digest,
            expected_size=len(attestation_bytes),
        )
        imported.append(
            A2AImportedArtifact(
                artifact_id=_local_artifact_id(
                    work,
                    remote_id="remote-attestation",
                    digest=attestation_digest,
                ),
                remote_artifact_id=None,
                kind=ArtifactKind.REPORT,
                media_type=_ATTESTATION_MEDIA_TYPE,
                published=attestation,
            )
        )

        for artifact in imported:
            registered = await self._control.register_artifact(
                work.mission_id,
                work.work_unit_id,
                runner_id=self._runner_id,
                lease_id=work.lease_id,
                artifact=artifact.published,
                artifact_id=artifact.artifact_id,
                kind=artifact.kind.value,
                media_type=artifact.media_type,
            )
            _assert_registered_artifact(registered, work, artifact)
        return A2AOutboundResultImport(artifacts=tuple(imported))


def parse_a2a_result_bundle(
    payload: Mapping[str, Any],
    *,
    expected_task_id: str,
) -> A2AValidatedResultBundle:
    if not isinstance(payload, Mapping):
        raise A2AOutboundResultError("remote A2A result must be an object")
    try:
        projection_size = len(
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise A2AOutboundResultError(
            "remote A2A result is not bounded JSON data"
        ) from exc
    if projection_size > _MAX_PROJECTION_BYTES:
        raise A2AOutboundResultError("remote A2A result exceeds the projection limit")
    try:
        task = _RemoteCompletedTask.model_validate(payload)
    except ValidationError as exc:
        raise A2AOutboundResultError(
            "remote A2A result failed schema validation"
        ) from exc
    if task.id != expected_task_id:
        raise A2AOutboundResultError("remote A2A result changed the task id")

    artifact_ids = [artifact.artifact_id for artifact in task.artifacts]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise A2AOutboundResultError("remote A2A result has duplicate Artifact ids")
    evidence_ids = [evidence.evidence_id for evidence in task.evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise A2AOutboundResultError("remote A2A result has duplicate Evidence ids")
    evidence_work_units = {evidence.work_unit_id for evidence in task.evidence}
    if len(evidence_work_units) != 1:
        raise A2AOutboundResultError(
            "remote A2A Evidence spans multiple WorkUnits"
        )
    if task.work_unit_id is not None and task.work_unit_id not in evidence_work_units:
        raise A2AOutboundResultError(
            "remote A2A Evidence does not match the result WorkUnit"
        )

    decoded: list[A2ARemoteArtifactContent] = []
    total_bytes = 0
    artifact_digests: dict[str, str] = {}
    for artifact in task.artifacts:
        file_part = artifact.file_part.file
        data_part = artifact.data_part.data
        try:
            encoded = file_part.encoded_bytes.encode("ascii")
            content = base64.b64decode(encoded, validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise A2AOutboundResultError(
                "remote A2A Artifact contains invalid Base64"
            ) from exc
        if base64.b64encode(content) != encoded:
            raise A2AOutboundResultError(
                "remote A2A Artifact Base64 is not canonical"
            )
        if len(content) != data_part.size_bytes:
            raise A2AOutboundResultError(
                "remote A2A Artifact size does not match its bytes"
            )
        digest = _sha256_digest(content)
        if digest.lower() != data_part.digest.lower():
            raise A2AOutboundResultError(
                "remote A2A Artifact digest does not match its bytes"
            )
        total_bytes += len(content)
        if total_bytes > _MAX_RAW_BYTES:
            raise A2AOutboundResultError(
                "remote A2A result exceeds the raw byte limit"
            )
        normalized_digest = digest.lower()
        try:
            artifact_kind = ArtifactKind(data_part.kind)
        except ValueError as exc:
            raise A2AOutboundResultError(
                "remote A2A Artifact has an unsupported kind"
            ) from exc
        artifact_digests[artifact.artifact_id] = normalized_digest
        decoded.append(
            A2ARemoteArtifactContent(
                remote_artifact_id=artifact.artifact_id,
                kind=artifact_kind,
                media_type=file_part.mime_type,
                digest=normalized_digest,
                content=content,
            )
        )

    referenced_ids: set[str] = set()
    for evidence in task.evidence:
        for reference in evidence.artifact_refs:
            digest = artifact_digests.get(reference.id)
            if digest is None:
                raise A2AOutboundResultError(
                    "remote Evidence references an unavailable Artifact"
                )
            if digest != reference.digest.lower():
                raise A2AOutboundResultError(
                    "remote Evidence Artifact digest does not match"
                )
            referenced_ids.add(reference.id)
    if referenced_ids != set(artifact_digests):
        raise A2AOutboundResultError(
            "remote A2A result contains an unreferenced Artifact"
        )

    evidence_projection = tuple(
        evidence.model_dump(by_alias=True, mode="json") for evidence in task.evidence
    )
    return A2AValidatedResultBundle(
        remote_task_id=task.id,
        artifacts=tuple(decoded),
        evidence=evidence_projection,
    )


def _attestation_bytes(
    work: A2AOutboundClaimedWork,
    bundle: A2AValidatedResultBundle,
    imported: list[A2AImportedArtifact],
) -> bytes:
    local_by_remote = {
        artifact.remote_artifact_id: artifact
        for artifact in imported
        if artifact.remote_artifact_id is not None
    }
    report = {
        "version": 1,
        "type": "a2a.remote-attestation",
        "authority": "remote-attestation-only",
        "remoteTaskId": bundle.remote_task_id,
        "localAttempt": {
            "missionId": work.mission_id,
            "workUnitId": work.work_unit_id,
            "attempt": work.attempt,
        },
        "artifacts": [
            {
                "remoteArtifactId": artifact.remote_artifact_id,
                "localArtifactId": local_by_remote[
                    artifact.remote_artifact_id
                ].artifact_id,
                "kind": artifact.kind.value,
                "mediaType": artifact.media_type,
                "digest": artifact.digest,
                "sizeBytes": len(artifact.content),
            }
            for artifact in bundle.artifacts
        ],
        "evidence": list(bundle.evidence),
    }
    encoded = json.dumps(
        report,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_ATTESTATION_BYTES:
        raise A2AOutboundResultError(
            "remote A2A attestation report exceeds the byte limit"
        )
    return encoded


def _assert_published_artifact(
    published: PublishedArtifact,
    *,
    expected_digest: str,
    expected_size: int,
) -> None:
    if not isinstance(published, PublishedArtifact):
        raise A2AOutboundResultError("Artifact publisher returned invalid metadata")
    digest_hex = expected_digest.removeprefix("sha256:")
    if (
        published.digest.lower() != expected_digest.lower()
        or published.size_bytes != expected_size
        or (
            expected_digest not in published.content_address
            and digest_hex not in published.content_address
        )
    ):
        raise A2AOutboundResultError(
            "Artifact publisher returned inconsistent metadata"
        )


def _assert_registered_artifact(
    payload: Mapping[str, Any],
    work: A2AOutboundClaimedWork,
    artifact: A2AImportedArtifact,
) -> None:
    if not isinstance(payload, Mapping):
        raise A2AOutboundResultError(
            "Mission Control returned an invalid Artifact response"
        )
    expected = {
        "id": artifact.artifact_id,
        "missionId": work.mission_id,
        "workUnitId": work.work_unit_id,
        "attempt": work.attempt,
        "kind": artifact.kind.value,
        "digest": artifact.published.digest,
        "contentAddress": artifact.published.content_address,
        "mediaType": artifact.media_type,
        "sizeBytes": artifact.published.size_bytes,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise A2AOutboundResultError(
            "Mission Control returned inconsistent Artifact metadata"
        )


def _local_artifact_id(
    work: A2AOutboundClaimedWork,
    *,
    remote_id: str,
    digest: str,
) -> str:
    identity = "\0".join(
        (
            work.mission_id,
            work.work_unit_id,
            str(work.attempt),
            remote_id,
            digest.lower(),
        )
    ).encode("utf-8")
    return f"artifact-a2a-{hashlib.sha256(identity).hexdigest()}"


def _sha256_digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _validate_media_type(value: str) -> None:
    if (
        value != value.strip()
        or "/" not in value
        or any(character in value for character in "\r\n\0")
    ):
        raise ValueError("remote Artifact has an invalid media type")


__all__ = [
    "A2AImportedArtifact",
    "A2AOutboundResultError",
    "A2AOutboundResultImport",
    "A2AOutboundResultImporter",
    "A2AOutboundResultImporterPort",
    "A2ARemoteArtifactContent",
    "A2AValidatedResultBundle",
    "parse_a2a_result_bundle",
]
