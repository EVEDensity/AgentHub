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


class MissionCheckpointMixin:
    async def publish_streaming_event(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        event_id: str,
        event_type: str,
        text: str,
        tool_name: str = "",
        attempt: int,
        lease_id: str,
        runner_id: str,
        actor: ActorRef,
    ) -> dict[str, Any]:
        """Append a bounded model-stream event behind the exact lease."""
        if event_type not in {
            "harness.assistant.delta", "harness.assistant.completed",
            "harness.tool.started", "harness.tool.output", "harness.tool.completed",
        }:
            raise ValueError("unsupported streaming event type")
        if event_type.startswith("harness.tool.") and not tool_name.strip():
            raise ValueError("tool streaming events require tool_name")
        if len(text) > 4000:
            raise ValueError("streaming event text exceeds 4000 characters")
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if mission is None or work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            if work_unit.lease is None or work_unit.lease.id != lease_id or work_unit.lease.runner_id != runner_id:
                raise LeaseOwnershipError("streaming event lease mismatch")
            if work_unit.attempt != attempt or work_unit.status not in {WorkUnitStatus.LEASED, WorkUnitStatus.RUNNING}:
                raise WorkUnitNotReadyError("streaming event attempt is not active")
            existing = next((e for e in await repository.list_work_unit_events(mission_id, limit=1000) if e.event_id == event_id), None)
            if existing is not None:
                if existing.aggregate_id != work_unit_id or existing.event_type != event_type:
                    raise ValueError("streaming event id already exists with different content")
                return existing.to_public_dict()
            sequence = await repository.get_last_event_sequence(work_unit_id, aggregate_type="work_unit") + 1
            event = EventEnvelope(
                event_id=event_id,
                aggregate_type="work_unit",
                aggregate_id=work_unit_id,
                sequence=sequence,
                event_type=event_type,
                actor=actor,
                occurred_at=datetime.now(timezone.utc),
                correlation_id=mission_id,
                payload={"attempt": attempt, "text": text, **({"toolName": tool_name} if tool_name else {})},
                schema_version=1,
            )
            await repository.append_event(event)
            return event.to_public_dict()
    """Mixin holding MissionService execution checkpoint methods."""

    async def record_execution_checkpoint(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        checkpoint_id: str,
        lease_id: str,
        runner_id: str,
        sequence: int,
        phase: ExecutionCheckpointPhase,
        iteration: int,
        tool_calls: int,
        prompt_tokens: int,
        completion_tokens: int,
        model_cost: float,
        terminal: bool,
        failure_reason: str | None,
        actor: ActorRef,
        tool_name: str | None = None,
        tool_success: bool | None = None,
    ) -> ExecutionCheckpoint:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError(
                    "execution checkpoints require a RUNNING mission"
                )
            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            if work_unit.status != WorkUnitStatus.RUNNING:
                raise WorkUnitNotReadyError(
                    "execution checkpoints require a RUNNING work unit"
                )
            if work_unit.lease is None:
                raise LeaseOwnershipError("work unit has no active lease")
            if (
                work_unit.lease.id != lease_id
                or work_unit.lease.runner_id != runner_id
            ):
                raise LeaseOwnershipError("work unit lease ownership mismatch")
            occurred_at = datetime.now(timezone.utc)
            if work_unit.lease.expires_at <= occurred_at:
                raise LeaseExpiredError("work unit lease has expired")

            requested = {
                "mission_id": mission_id,
                "work_unit_id": work_unit_id,
                "attempt": work_unit.attempt,
                "sequence": sequence,
                "phase": phase,
                "iteration": iteration,
                "tool_calls": tool_calls,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "model_cost": model_cost,
                "terminal": terminal,
                "failure_reason": failure_reason,
            }
            existing = await repository.get_execution_checkpoint(checkpoint_id)
            if existing is not None:
                if any(
                    getattr(existing, field_name) != value
                    for field_name, value in requested.items()
                ):
                    raise ValueError(
                        "execution checkpoint id already exists with different content"
                    )
                return existing

            latest = await repository.get_latest_execution_checkpoint(
                work_unit_id,
                work_unit.attempt,
            )
            if latest is not None and latest.terminal:
                raise ValueError("terminal execution checkpoint already exists")
            expected_sequence = 1 if latest is None else latest.sequence + 1
            if sequence != expected_sequence:
                raise ValueError(
                    "execution checkpoint sequence must be contiguous "
                    f"(expected={expected_sequence}, actual={sequence})"
                )
            if sequence == 1 and phase != ExecutionCheckpointPhase.EXECUTION_STARTED:
                raise ValueError(
                    "first execution checkpoint must mark execution start"
                )

            digest_material = {
                "attempt": work_unit.attempt,
                "completionTokens": completion_tokens,
                "failureReason": failure_reason,
                "iteration": iteration,
                "missionId": mission_id,
                "modelCost": model_cost,
                "phase": phase.value,
                "promptTokens": prompt_tokens,
                "sequence": sequence,
                "terminal": terminal,
                "toolCalls": tool_calls,
                "workUnitId": work_unit_id,
            }
            state_digest = "sha256:" + hashlib.sha256(
                json.dumps(
                    digest_material,
                    allow_nan=False,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            checkpoint = ExecutionCheckpoint(
                id=checkpoint_id,
                **requested,
                state_digest=state_digest,
                created_by=actor,
                created_at=occurred_at,
            )
            await repository.add_execution_checkpoint(checkpoint)
            event_sequence = (
                await repository.get_last_event_sequence(
                    work_unit_id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            await repository.append_event(
                EventEnvelope(
                    event_id=new_identifier("evt"),
                    aggregate_type="work_unit",
                    aggregate_id=work_unit_id,
                    sequence=event_sequence,
                    event_type="work_unit.checkpoint.recorded",
                    actor=actor,
                    occurred_at=occurred_at,
                    correlation_id=mission_id,
                    payload=_checkpoint_event_payload(
                        checkpoint,
                        tool_name=tool_name,
                        tool_success=tool_success,
                    ),
                    schema_version=1,
                )
            )
        return checkpoint

    async def _transition_execution_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        target: WorkUnitStatus,
        event_type: str,
        lease_id: str,
        runner_id: str,
        actor: ActorRef,
        reason: str | None = None,
        artifact_refs: list[ArtifactRef] | None = None,
    ) -> WorkUnit:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError("work units require a RUNNING mission")

            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            if work_unit.lease is None:
                raise LeaseOwnershipError("work unit has no active lease")
            if work_unit.lease.id != lease_id:
                raise LeaseOwnershipError("lease id does not match the work unit")
            if work_unit.lease.runner_id != runner_id:
                raise LeaseOwnershipError("lease belongs to another runner")

            occurred_at = datetime.now(timezone.utc)
            if work_unit.lease.expires_at <= occurred_at:
                raise LeaseExpiredError("work unit lease has expired")
            if target == WorkUnitStatus.RETRYING:
                contract = await repository.get_contract(
                    mission.contract_id, mission.contract_version
                )
                if contract is None:
                    raise WorkUnitNotReadyError("mission contract not found")
                if work_unit.attempt >= contract.budgets.retries + 1:
                    raise WorkUnitNotReadyError("work unit retry budget is exhausted")
            if target == WorkUnitStatus.VERIFYING and not artifact_refs:
                raise WorkUnitNotReadyError(
                    "work unit verification requires at least one artifact"
                )
            if target == WorkUnitStatus.VERIFYING:
                await self._validate_artifact_refs(
                    repository,
                    mission_id,
                    artifact_refs or [],
                    work_unit_id=work_unit.id,
                    attempt=work_unit.attempt,
                )
            updated = transition_work_unit(
                work_unit,
                target,
                occurred_at=occurred_at,
            )
            sequence = (
                await repository.get_last_event_sequence(
                    work_unit.id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit.id,
                sequence=sequence,
                event_type=event_type,
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload={
                    "previousStatus": work_unit.status.value,
                    "status": updated.status.value,
                    "leaseId": lease_id,
                    "attempt": updated.attempt,
                    **(
                        {
                            "artifactRefs": [
                                ref.to_public_dict() for ref in artifact_refs
                            ]
                        }
                        if artifact_refs is not None
                        else {}
                    ),
                    **({"reason": reason} if reason is not None else {}),
                },
                schema_version=1,
            )
            await repository.update_work_unit(updated)
            await repository.append_event(event)
            if target == WorkUnitStatus.FAILED:
                await self._fail_mission_for_work_unit(
                    repository,
                    mission,
                    work_unit_id=work_unit.id,
                    actor=actor,
                    reason=reason or "work unit execution failed",
                    occurred_at=occurred_at,
                    causation_id=event.event_id,
                )
        return updated
