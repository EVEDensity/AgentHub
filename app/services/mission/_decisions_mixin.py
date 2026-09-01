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


class MissionDecisionsMixin:
    """Mixin holding MissionService decision lifecycle methods."""

    async def discover_workspace_verification_work(
        self,
        workspace_id: str,
    ) -> VerificationDiscoveryOutcome:
        """Return one authorized workspace's minimal, consistent verifier input."""

        normalized_workspace_id = workspace_id.strip()
        if not normalized_workspace_id:
            raise ValueError("workspace_id must be non-empty")
        async with self._repository.transaction() as repository:
            selection = await repository.get_workspace_verification_candidate(
                normalized_workspace_id
            )
            if selection is None:
                return VerificationDiscoveryOutcome(context=None)
            mission, work_unit = selection
            if mission.workspace_id != normalized_workspace_id:
                raise WorkUnitNotReadyError(
                    "verification repository returned another workspace"
                )
            if mission.status not in {MissionStatus.RUNNING, MissionStatus.VERIFYING}:
                raise WorkUnitNotReadyError(
                    "verification requires a RUNNING or VERIFYING mission"
                )
            if (
                work_unit.mission_id != mission.id
                or work_unit.status != WorkUnitStatus.VERIFYING
            ):
                raise WorkUnitNotReadyError(
                    "verification repository returned an ineligible WorkUnit"
                )
            if work_unit.attempt < 1:
                raise WorkUnitNotReadyError(
                    "verification requires a positive WorkUnit attempt"
                )
            contract = await repository.get_contract(
                mission.contract_id, mission.contract_version
            )
            if contract is None:
                raise WorkUnitNotReadyError("mission contract not found")
            artifacts = await repository.list_work_unit_artifacts(
                mission.id,
                work_unit.id,
                work_unit.attempt,
                limit=_MAX_VERIFICATION_ARTIFACTS + 1,
            )
            if not artifacts:
                raise WorkUnitNotReadyError(
                    "verification requires current-attempt Artifacts"
                )
            if len(artifacts) > _MAX_VERIFICATION_ARTIFACTS:
                raise WorkUnitNotReadyError("verification Artifact count exceeds 200")
            if len({artifact.id for artifact in artifacts}) != len(artifacts):
                raise WorkUnitNotReadyError(
                    "verification repository returned duplicate Artifacts"
                )
            if any(
                artifact.mission_id != mission.id
                or artifact.work_unit_id != work_unit.id
                or artifact.attempt != work_unit.attempt
                for artifact in artifacts
            ):
                raise WorkUnitNotReadyError(
                    "verification repository returned unrelated Artifacts"
                )
            evaluation_policy = self._verification_policy_resolver.resolve(
                contract,
                work_unit,
                tuple(artifacts),
            )
            context = VerificationContext(
                mission=mission,
                contract=contract,
                work_unit=work_unit,
                artifacts=tuple(artifacts),
                evaluation_policy=evaluation_policy,
            )
            if evaluation_policy.plan is None:
                await self._request_verification_decision(
                    repository,
                    mission=mission,
                    contract=contract,
                    work_unit=work_unit,
                    artifacts=tuple(artifacts),
                    evaluation_policy=evaluation_policy,
                )
            return VerificationDiscoveryOutcome(context=context)

    async def _request_verification_decision(
        self,
        repository: MissionRepository,
        *,
        mission: Mission,
        contract: MissionContract,
        work_unit: WorkUnit,
        artifacts: tuple[Artifact, ...],
        evaluation_policy: EvaluationPolicyDecision,
    ) -> Decision:
        if evaluation_policy.plan is not None or evaluation_policy.reason is None:
            raise ValueError("verification Decision requires an inconclusive policy")
        occurred_at = datetime.now(timezone.utc)
        can_retry = work_unit.attempt < contract.budgets.retries + 1
        options = (
            (DecisionResolution.RETRY_WORK_UNIT, DecisionResolution.FAIL_MISSION)
            if can_retry
            else (DecisionResolution.FAIL_MISSION,)
        )
        recommended_option = (
            DecisionResolution.RETRY_WORK_UNIT
            if can_retry
            and evaluation_policy.reason.value == "artifact_requirements_not_met"
            else DecisionResolution.FAIL_MISSION
        )
        material = {
            "schemaVersion": 1,
            "missionId": mission.id,
            "contractId": contract.id,
            "contractVersion": contract.version,
            "workUnitId": work_unit.id,
            "workUnitKind": work_unit.kind,
            "attempt": work_unit.attempt,
            "reasonCode": evaluation_policy.reason.value,
            "criterionIds": list(evaluation_policy.criterion_ids),
            "artifacts": [
                {
                    "id": artifact.id,
                    "digest": artifact.digest.lower(),
                    "sizeBytes": artifact.size_bytes,
                }
                for artifact in sorted(artifacts, key=lambda item: item.id)
            ],
        }
        encoded_material = json.dumps(
            material,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        decision = Decision(
            id=new_identifier("dec"),
            mission_id=mission.id,
            work_unit_id=work_unit.id,
            attempt=work_unit.attempt,
            context_digest="sha256:" + hashlib.sha256(encoded_material).hexdigest(),
            reason_code=evaluation_policy.reason.value,
            criterion_ids=evaluation_policy.criterion_ids,
            options=options,
            recommended_option=recommended_option,
            risk_summary=(
                "Independent verification cannot prove the affected Contract "
                "criteria under the current policy and Artifact set."
            ),
            status=DecisionStatus.PENDING,
            version=1,
            requested_by=ActorRef(type=ActorType.SERVICE, id="mission-control"),
            requested_at=occurred_at,
            expires_at=occurred_at
            + timedelta(seconds=contract.governance.decision_timeout_seconds),
        )
        decision_event = EventEnvelope(
            event_id=new_identifier("evt"),
            aggregate_type="decision",
            aggregate_id=decision.id,
            sequence=1,
            event_type="decision.lifecycle.requested",
            actor=decision.requested_by,
            occurred_at=occurred_at,
            correlation_id=mission.id,
            payload=decision.to_public_dict(),
            schema_version=1,
        )
        updated_mission = transition_mission(
            mission,
            MissionStatus.WAITING_DECISION,
            occurred_at=occurred_at,
        )
        mission_sequence = await repository.get_last_event_sequence(mission.id) + 1
        mission_event = EventEnvelope(
            event_id=new_identifier("evt"),
            aggregate_type="mission",
            aggregate_id=mission.id,
            sequence=mission_sequence,
            event_type="mission.lifecycle.waiting_decision",
            actor=decision.requested_by,
            occurred_at=occurred_at,
            correlation_id=mission.id,
            causation_id=decision_event.event_id,
            payload={
                "previousStatus": mission.status.value,
                "status": updated_mission.status.value,
                "decisionId": decision.id,
                "workUnitId": work_unit.id,
            },
            schema_version=1,
        )
        await repository.add_decision(decision)
        await repository.append_event(decision_event)
        await repository.update_mission(updated_mission)
        await repository.append_event(mission_event)
        return decision

    async def resolve_decision(
        self,
        mission_id: str,
        decision_id: str,
        *,
        expected_version: int,
        resolution: DecisionResolution,
        rationale: str,
        actor: ActorRef,
    ) -> tuple[Decision, WorkUnit, Mission]:
        if actor.type != ActorType.HUMAN:
            raise ValueError("only human actors can resolve Decisions")
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            decision = await repository.get_decision_for_update(decision_id)
            if decision is None or decision.mission_id != mission_id:
                raise DecisionNotFoundError(decision_id)
            if decision.status != DecisionStatus.PENDING:
                raise DecisionConflictError("Decision is already resolved")
            if decision.version != expected_version:
                raise DecisionConflictError("Decision version conflict")
            if resolution not in decision.options:
                raise DecisionConflictError("Decision resolution is not offered")
            if mission.status != MissionStatus.WAITING_DECISION:
                raise DecisionConflictError(
                    "Mission is not waiting for a Decision"
                )
            occurred_at = datetime.now(timezone.utc)
            if decision.expires_at is not None and decision.expires_at <= occurred_at:
                raise DecisionConflictError("Decision has expired")
            work_unit = await repository.get_work_unit_for_update(
                decision.work_unit_id
            )
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(decision.work_unit_id)
            if (
                work_unit.status != WorkUnitStatus.VERIFYING
                or work_unit.attempt != decision.attempt
            ):
                raise DecisionConflictError(
                    "Decision no longer matches the verifying WorkUnit attempt"
                )

            if resolution == DecisionResolution.RETRY_WORK_UNIT:
                contract = await repository.get_contract(
                    mission.contract_id, mission.contract_version
                )
                if contract is None:
                    raise WorkUnitNotReadyError("mission contract not found")
                if work_unit.attempt >= contract.budgets.retries + 1:
                    raise DecisionConflictError("work unit retry budget is exhausted")

            resolved_decision = Decision.model_validate(
                {
                    **decision.model_dump(),
                    "status": DecisionStatus.RESOLVED,
                    "version": decision.version + 1,
                    "resolution": resolution,
                    "rationale": rationale,
                    "resolved_by": actor,
                    "resolved_at": occurred_at,
                }
            )
            decision_sequence = (
                await repository.get_last_event_sequence(
                    decision.id,
                    aggregate_type="decision",
                )
                + 1
            )
            decision_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="decision",
                aggregate_id=decision.id,
                sequence=decision_sequence,
                event_type="decision.lifecycle.resolved",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission.id,
                payload={
                    "previousStatus": decision.status.value,
                    "status": resolved_decision.status.value,
                    "previousVersion": decision.version,
                    "version": resolved_decision.version,
                    "resolution": resolution.value,
                },
                schema_version=1,
            )
            work_unit_target = (
                WorkUnitStatus.RETRYING
                if resolution == DecisionResolution.RETRY_WORK_UNIT
                else WorkUnitStatus.FAILED
            )
            updated_work_unit = transition_work_unit(
                work_unit,
                work_unit_target,
                occurred_at=occurred_at,
            )
            work_unit_sequence = (
                await repository.get_last_event_sequence(
                    work_unit.id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            work_unit_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit.id,
                sequence=work_unit_sequence,
                event_type="work_unit.lifecycle.decision_resolved",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission.id,
                causation_id=decision_event.event_id,
                payload={
                    "previousStatus": work_unit.status.value,
                    "status": updated_work_unit.status.value,
                    "decisionId": decision.id,
                    "resolution": resolution.value,
                },
                schema_version=1,
            )
            mission_target = (
                MissionStatus.RUNNING
                if resolution == DecisionResolution.RETRY_WORK_UNIT
                else MissionStatus.FAILED
            )
            updated_mission = transition_mission(
                mission,
                mission_target,
                occurred_at=occurred_at,
            )
            mission_sequence = await repository.get_last_event_sequence(mission.id) + 1
            mission_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="mission",
                aggregate_id=mission.id,
                sequence=mission_sequence,
                event_type="mission.lifecycle.decision_resolved",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission.id,
                causation_id=work_unit_event.event_id,
                payload={
                    "previousStatus": mission.status.value,
                    "status": updated_mission.status.value,
                    "decisionId": decision.id,
                    "resolution": resolution.value,
                },
                schema_version=1,
            )

            await repository.update_decision(resolved_decision)
            await repository.append_event(decision_event)
            await repository.update_work_unit(updated_work_unit)
            await repository.append_event(work_unit_event)
            await repository.update_mission(updated_mission)
            await repository.append_event(mission_event)
            return resolved_decision, updated_work_unit, updated_mission

    async def expire_next_decision(
        self,
        *,
        occurred_at: datetime | None = None,
    ) -> DecisionExpiryOutcome:
        expiration_time = occurred_at or datetime.now(timezone.utc)
        if expiration_time.tzinfo is None or expiration_time.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        actor = ActorRef(type=ActorType.SERVICE, id="mission-control")
        async with self._repository.transaction() as repository:
            candidate = await repository.get_expired_decision_candidate_for_update(
                expiration_time
            )
            if candidate is None:
                return DecisionExpiryOutcome(None, None, None)
            mission, decision = candidate
            if decision.mission_id != mission.id:
                raise DecisionConflictError(
                    "expired Decision candidate belongs to another Mission"
                )
            if mission.status != MissionStatus.WAITING_DECISION:
                raise DecisionConflictError(
                    "expired Decision Mission is not waiting for a Decision"
                )
            if decision.status != DecisionStatus.PENDING:
                raise DecisionConflictError("expired Decision candidate is not pending")
            if decision.expires_at is None or decision.expires_at > expiration_time:
                raise DecisionConflictError("Decision has not expired")

            work_unit = await repository.get_work_unit_for_update(
                decision.work_unit_id
            )
            if work_unit is None or work_unit.mission_id != mission.id:
                raise WorkUnitNotFoundError(decision.work_unit_id)
            if (
                work_unit.status != WorkUnitStatus.VERIFYING
                or work_unit.attempt != decision.attempt
            ):
                raise DecisionConflictError(
                    "expired Decision no longer matches the verifying WorkUnit attempt"
                )

            expired_decision = Decision.model_validate(
                {
                    **decision.model_dump(),
                    "status": DecisionStatus.EXPIRED,
                    "version": decision.version + 1,
                    "rationale": "Decision expired before human resolution.",
                    "resolved_by": actor,
                    "resolved_at": expiration_time,
                }
            )
            decision_sequence = (
                await repository.get_last_event_sequence(
                    decision.id,
                    aggregate_type="decision",
                )
                + 1
            )
            decision_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="decision",
                aggregate_id=decision.id,
                sequence=decision_sequence,
                event_type="decision.lifecycle.expired",
                actor=actor,
                occurred_at=expiration_time,
                correlation_id=mission.id,
                payload={
                    "previousStatus": decision.status.value,
                    "status": expired_decision.status.value,
                    "previousVersion": decision.version,
                    "version": expired_decision.version,
                    "expiresAt": decision.expires_at.isoformat(),
                },
                schema_version=1,
            )
            updated_work_unit = transition_work_unit(
                work_unit,
                WorkUnitStatus.FAILED,
                occurred_at=expiration_time,
            )
            work_unit_sequence = (
                await repository.get_last_event_sequence(
                    work_unit.id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            work_unit_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit.id,
                sequence=work_unit_sequence,
                event_type="work_unit.lifecycle.decision_expired",
                actor=actor,
                occurred_at=expiration_time,
                correlation_id=mission.id,
                causation_id=decision_event.event_id,
                payload={
                    "previousStatus": work_unit.status.value,
                    "status": updated_work_unit.status.value,
                    "decisionId": decision.id,
                },
                schema_version=1,
            )
            updated_mission = transition_mission(
                mission,
                MissionStatus.FAILED,
                occurred_at=expiration_time,
            )
            mission_sequence = await repository.get_last_event_sequence(mission.id) + 1
            mission_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="mission",
                aggregate_id=mission.id,
                sequence=mission_sequence,
                event_type="mission.lifecycle.decision_expired",
                actor=actor,
                occurred_at=expiration_time,
                correlation_id=mission.id,
                causation_id=work_unit_event.event_id,
                payload={
                    "previousStatus": mission.status.value,
                    "status": updated_mission.status.value,
                    "decisionId": decision.id,
                },
                schema_version=1,
            )

            await repository.update_decision(expired_decision)
            await repository.append_event(decision_event)
            await repository.update_work_unit(updated_work_unit)
            await repository.append_event(work_unit_event)
            await repository.update_mission(updated_mission)
            await repository.append_event(mission_event)
            return DecisionExpiryOutcome(
                expired_decision,
                updated_work_unit,
                updated_mission,
            )
