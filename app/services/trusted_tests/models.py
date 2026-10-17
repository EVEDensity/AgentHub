"""Versioned execution manifests and producer receipts, separate from reports."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.verification_report_models import parse_report

Digest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=255)]
Attempt = Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]
ArtifactRole = Literal["patch", "report", "log", "receipt", "manifest"]
T = TypeVar("T", bound=BaseModel)


def canonical_bytes(value: BaseModel | Mapping[str, Any]) -> bytes:
    document = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def content_digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def artifact_id(work_unit_id: str, attempt: int, role: ArtifactRole, digest: str) -> str:
    """Stable IDs remain bounded even when the WorkUnit ID is 255 characters."""
    if role not in {"patch", "report", "log", "receipt", "manifest"}:
        raise ValueError("unsupported trusted test artifact role")
    if type(attempt) is not int or not 1 <= attempt <= 2_147_483_647:
        raise ValueError("invalid trusted test attempt")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("invalid trusted test artifact digest")
    identity = hashlib.sha256(work_unit_id.encode("utf-8")).hexdigest()[:24]
    return f"trusted-{identity}-{attempt}-{role}-{digest[7:]}"


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schemaVersion: Literal[1]
    missionId: Identifier
    workUnitId: Identifier
    attempt: Attempt
    contractId: Identifier
    contractVersion: Annotated[int, Field(strict=True, ge=1)]
    configurationDigest: Digest
    executionMode: Literal["trusted-local"]
    inputSnapshotDigest: Digest
    candidateSnapshotDigest: Digest
    testProfileDigest: Digest

    @model_validator(mode="before")
    @classmethod
    def strict_version(cls, value):
        if isinstance(value, dict) and "schemaVersion" in value and type(value["schemaVersion"]) is not int:
            raise ValueError("schemaVersion must be an integer")
        return value


class ArtifactLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: Identifier
    digest: Digest


class ReceiptPayload(_Document):
    receiptType: Literal["trusted-test-run"]
    receiptId: Identifier
    producerId: Identifier
    keyId: Identifier
    patchDigest: Digest
    reportDigest: Digest
    logDigest: Digest
    exitCode: Annotated[int, Field(strict=True, ge=-65535, le=65535)] | None
    timedOut: Annotated[bool, Field(strict=True)]
    startedAt: Identifier
    finishedAt: Identifier

    @model_validator(mode="after")
    def completed_interval(self):
        start = datetime.fromisoformat(self.startedAt)
        finish = datetime.fromisoformat(self.finishedAt)
        if start.tzinfo is None or finish.tzinfo is None or finish < start:
            raise ValueError("receipt requires an ordered timezone-aware interval")
        if self.timedOut and self.exitCode == 0:
            raise ValueError("timed out execution cannot report zero exit")
        return self


class TrustedTestReceipt(ReceiptPayload):
    signature: Annotated[str, Field(strict=True, min_length=88, max_length=88)]


class ExecutionManifest(_Document):
    manifestType: Literal["trusted-test-execution"]
    patch: ArtifactLink
    report: ArtifactLink
    log: ArtifactLink
    receipt: ArtifactLink

    @model_validator(mode="after")
    def unique_references(self):
        if len({ref.id for ref in self.references()}) != 4:
            raise ValueError("execution manifest requires four unique Artifact refs")
        return self

    def references(self) -> tuple[ArtifactLink, ...]:
        return self.patch, self.report, self.log, self.receipt


def parse_document(content: bytes, model: type[T]) -> T:
    """Reuse bounded UTF-8 JSON parsing with duplicate/nonfinite rejection."""
    return parse_report(content, model)
