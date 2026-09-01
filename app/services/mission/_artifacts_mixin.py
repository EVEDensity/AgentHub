from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain import (
    ActorRef,
    ActorType,
    Artifact,
    ArtifactKind,
    ArtifactRef,
    ArtifactRetention,
    ArtifactSensitivity,
    Decision,
    DecisionResolution,
    DecisionStatus,
    EventEnvelope,
    Evidence,
    EvidenceVerdict,
    ExecutionCheckpoint,
    ExecutionCheckpointPhase,
    Lease,
    Mission,
    MissionContract,
    MissionSource,
    MissionSourceType,
    MissionStatus,
    OutputSpec,
    VerifierRef,
    WorkUnit,
    WorkUnitStatus,
    transition_mission,
    transition_work_unit,
)
from app.repositories import MissionRepository
from app.services.agent_binding_service import (
    AgentBindingResolver,
    AgentBindingUnavailableError,
)
from app.services.artifact_integrity_service import (
    ArtifactBytesUnavailableError,
    ArtifactByteVerifier,
)
from app.services.evidence_integrity_service import (
    EvidenceIntegrityHasher,
    EvidenceIntegrityMaterial,
    Sha256EvidenceIntegrityHasher,
)
from app.services.verification_evaluator_service import (
    StrictVerificationEvaluator,
    VerificationEvaluationResult,
    VerificationEvaluator,
    canonicalize_artifact_byte_verifications,
)
from app.services.verification_policy_service import (
    ArtifactSetEvaluationPlan,
    EvaluationPolicyDecision,
    StrictVerificationPolicyResolver,
    VerificationPolicyResolver,
)
from app.services.workspace_admission_service import (
    WorkspaceClaimAdmissionPolicy,
    WorkspaceClaimAdmissionUnavailableError,
    WorkspaceClaimStatus,
)

# Re-export types/errors/helpers from _types — MissionService uses them.
# Note: `from module import *` skips underscore-prefixed names, so we list
# the ones actually referenced by the MissionService implementation.
from app.services.mission._types import *  # noqa: F401,F403  # surface errors/helpers
from app.services.mission._types import (
    _A2A_OUTBOUND_ADAPTER,
    _DESKTOP_TASK_WORK_UNIT_KIND,
    _MAX_VERIFICATION_ARTIFACTS,
    _VERIFICATION_ARTIFACT_FIELDS,
    _checkpoint_event_payload,
)


class MissionArtifactsMixin:
    """Mixin holding MissionService artifact registration methods."""

    async def register_artifact(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        artifact_id: str,
        lease_id: str,
        runner_id: str,
        kind: ArtifactKind,
        digest: str,
        content_address: str,
        media_type: str,
        size_bytes: int,
        source_repository: str | None,
        base_commit: str | None,
        retention: ArtifactRetention,
        sensitivity: ArtifactSensitivity,
        actor: ActorRef,
    ) -> Artifact:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError("artifacts require a RUNNING mission")

            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            if work_unit.status != WorkUnitStatus.RUNNING:
                raise WorkUnitNotReadyError(
                    "artifacts can only be registered for a RUNNING work unit"
                )
            if work_unit.lease is None:
                raise LeaseOwnershipError("work unit has no active lease")
            if work_unit.lease.id != lease_id:
                raise LeaseOwnershipError("lease id does not match the work unit")
            if work_unit.lease.runner_id != runner_id:
                raise LeaseOwnershipError("lease belongs to another runner")

            occurred_at = datetime.now(timezone.utc)
            if work_unit.lease.expires_at <= occurred_at:
                raise LeaseExpiredError("work unit lease has expired")
            digest_value = digest.removeprefix("sha256:")
            if digest not in content_address and digest_value not in content_address:
                raise ValueError("artifact content address must include its digest")

            existing = await repository.get_artifact(artifact_id)
            expected_values = {
                "mission_id": mission_id,
                "work_unit_id": work_unit_id,
                "attempt": work_unit.attempt,
                "kind": kind,
                "digest": digest,
                "content_address": content_address,
                "media_type": media_type,
                "size_bytes": size_bytes,
                "source_repository": source_repository,
                "base_commit": base_commit,
                "retention": retention,
                "sensitivity": sensitivity,
                "created_by": actor,
            }
            if existing is not None:
                if all(
                    getattr(existing, field_name) == value
                    for field_name, value in expected_values.items()
                ):
                    return existing
                raise ValueError("artifact id already exists with different metadata")

            artifact = Artifact(
                id=artifact_id,
                created_at=occurred_at,
                **expected_values,
            )
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="artifact",
                aggregate_id=artifact.id,
                sequence=1,
                event_type="artifact.lifecycle.registered",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload=artifact.to_public_dict(),
                schema_version=1,
            )
            await repository.add_artifact(artifact)
            await repository.append_event(event)
        return artifact

    async def _validate_artifact_refs(
        self,
        repository: MissionRepository,
        mission_id: str,
        artifact_refs: list[ArtifactRef],
        *,
        work_unit_id: str | None = None,
        attempt: int | None = None,
    ) -> list[Artifact]:
        artifact_ids = [artifact_ref.id for artifact_ref in artifact_refs]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise WorkUnitNotReadyError("artifact references must be unique")
        artifacts: list[Artifact] = []
        for artifact_ref in artifact_refs:
            artifact = await repository.get_artifact(artifact_ref.id)
            if artifact is None:
                raise WorkUnitNotReadyError(
                    f"artifact is not registered: {artifact_ref.id}"
                )
            if artifact.mission_id != mission_id:
                raise WorkUnitNotReadyError(
                    f"artifact belongs to another mission: {artifact_ref.id}"
                )
            if artifact.digest.lower() != artifact_ref.digest.lower():
                raise WorkUnitNotReadyError(
                    f"artifact digest does not match: {artifact_ref.id}"
                )
            if work_unit_id is not None and artifact.work_unit_id != work_unit_id:
                raise WorkUnitNotReadyError(
                    f"artifact belongs to another work unit: {artifact_ref.id}"
                )
            if attempt is not None and artifact.attempt != attempt:
                raise WorkUnitNotReadyError(
                    f"artifact belongs to another attempt: {artifact_ref.id}"
                )
            artifacts.append(artifact)
        return artifacts
