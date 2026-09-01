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


class MissionVerificationMixin:
    """Mixin holding MissionService verification methods."""

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

    async def verify_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        criterion_id: str,
        verifier_id: str,
        verifier_version: str,
        configuration_digest: str | None,
        verdict: EvidenceVerdict,
        artifact_refs: list[ArtifactRef],
        summary: str,
        actor: ActorRef,
    ) -> tuple[Evidence, WorkUnit, Mission]:
        if actor.type != ActorType.VERIFIER:
            raise ValueError("only verifier actors can record Evidence")
        work_unit = await self._repository.get_work_unit(work_unit_id)
        if work_unit is None or work_unit.mission_id != mission_id:
            raise WorkUnitNotFoundError(work_unit_id)
        if work_unit.status != WorkUnitStatus.VERIFYING:
            raise WorkUnitNotReadyError(
                "Evidence can only be recorded for a VERIFYING work unit"
            )
        mission = await self._repository.get_mission(mission_id)
        if mission is None:
            raise MissionNotFoundError(mission_id)
        if mission.status not in {MissionStatus.RUNNING, MissionStatus.VERIFYING}:
            raise WorkUnitNotReadyError(
                "mission must be RUNNING or VERIFYING to record Evidence"
            )
        contract = await self._repository.get_contract(
            mission.contract_id, mission.contract_version
        )
        if contract is None:
            raise WorkUnitNotReadyError("mission contract not found")
        if criterion_id not in {
            criterion.id for criterion in contract.acceptance_criteria
        }:
            raise WorkUnitNotReadyError(
                "Evidence criterion is not part of the mission contract"
            )
        if verdict == EvidenceVerdict.INCONCLUSIVE:
            raise WorkUnitNotReadyError(
                "INCONCLUSIVE verification requires a Mission Decision"
            )
        verification_attempt = work_unit.attempt if work_unit.attempt > 0 else None
        artifacts = await self._validate_artifact_refs(
            self._repository,
            mission_id,
            artifact_refs,
            work_unit_id=work_unit_id,
            attempt=verification_attempt,
        )
        evaluation_plan: ArtifactSetEvaluationPlan | None = None
        if verdict == EvidenceVerdict.PASS:
            evaluation_plan = self._admit_pass_evidence(
                contract=contract,
                work_unit=work_unit,
                artifacts=tuple(artifacts),
                criterion_id=criterion_id,
                configuration_digest=configuration_digest,
            )
        if self._artifact_byte_verifier is None:
            raise ArtifactBytesUnavailableError(
                "artifact byte verifier is not configured"
            )
        byte_verifications = await self._artifact_byte_verifier.verify_all(artifacts)
        evaluation_result: VerificationEvaluationResult | None = None
        if evaluation_plan is not None:
            evaluation_result = self._verification_evaluator.evaluate(
                evaluation_plan,
                tuple(artifacts),
                tuple(byte_verifications),
            )

        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status not in {MissionStatus.RUNNING, MissionStatus.VERIFYING}:
                raise WorkUnitNotReadyError(
                    "mission must be RUNNING or VERIFYING to record Evidence"
                )

            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            if work_unit.status != WorkUnitStatus.VERIFYING:
                raise WorkUnitNotReadyError(
                    "Evidence can only be recorded for a VERIFYING work unit"
                )

            contract = await repository.get_contract(
                mission.contract_id, mission.contract_version
            )
            if contract is None:
                raise WorkUnitNotReadyError("mission contract not found")
            if criterion_id not in {
                criterion.id for criterion in contract.acceptance_criteria
            }:
                raise WorkUnitNotReadyError(
                    "Evidence criterion is not part of the mission contract"
                )
            current_artifacts = await self._validate_artifact_refs(
                repository,
                mission_id,
                artifact_refs,
                work_unit_id=work_unit.id,
                attempt=work_unit.attempt if work_unit.attempt > 0 else None,
            )
            if current_artifacts != artifacts:
                raise WorkUnitNotReadyError(
                    "artifact metadata changed during byte verification"
                )
            admitted_evaluation: VerificationEvaluationResult | None = None
            if verdict == EvidenceVerdict.PASS:
                current_plan = self._admit_pass_evidence(
                    contract=contract,
                    work_unit=work_unit,
                    artifacts=tuple(current_artifacts),
                    criterion_id=criterion_id,
                    configuration_digest=configuration_digest,
                )
                current_evaluation = self._verification_evaluator.evaluate(
                    current_plan,
                    tuple(current_artifacts),
                    tuple(byte_verifications),
                )
                if current_evaluation != evaluation_result:
                    raise WorkUnitNotReadyError(
                        "verification evaluation changed before Evidence admission"
                    )
                admitted_evaluation = current_evaluation

            occurred_at = datetime.now(timezone.utc)
            evidence_id = new_identifier("evd")
            verifier = VerifierRef(
                id=verifier_id,
                version=verifier_version,
                configuration_digest=configuration_digest,
            )
            integrity_hash = self._evidence_integrity_hasher.compute(
                EvidenceIntegrityMaterial(
                    evidence_id=evidence_id,
                    mission_id=mission_id,
                    contract_id=contract.id,
                    contract_version=contract.version,
                    work_unit_id=work_unit_id,
                    work_unit_attempt=work_unit.attempt,
                    criterion_id=criterion_id,
                    verifier=verifier,
                    verdict=verdict,
                    artifact_refs=tuple(artifact_refs),
                    artifacts=tuple(current_artifacts),
                    byte_verifications=tuple(byte_verifications),
                    evaluation=admitted_evaluation,
                    summary=summary,
                    generated_at=occurred_at,
                )
            )
            evidence = Evidence(
                id=evidence_id,
                mission_id=mission_id,
                work_unit_id=work_unit_id,
                criterion_id=criterion_id,
                verifier=verifier,
                verdict=verdict,
                artifact_refs=artifact_refs,
                summary=summary,
                generated_at=occurred_at,
                integrity_hash=integrity_hash,
            )
            evidence_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="evidence",
                aggregate_id=evidence.id,
                sequence=1,
                event_type="evidence.lifecycle.recorded",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload=evidence.to_public_dict(),
                schema_version=1,
            )
            await repository.add_evidence(evidence)
            await repository.append_event(evidence_event)

            if verdict == EvidenceVerdict.PASS:
                work_unit_event_type = "work_unit.lifecycle.verified"
                work_unit_target = WorkUnitStatus.SUCCEEDED
            else:
                work_unit_event_type = "work_unit.lifecycle.verification_failed"
                work_unit_target = WorkUnitStatus.FAILED
            updated_work_unit = transition_work_unit(
                work_unit,
                work_unit_target,
                occurred_at=occurred_at,
            )
            await repository.update_work_unit(updated_work_unit)
            work_unit_sequence = (
                await repository.get_last_event_sequence(
                    work_unit_id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            work_unit_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit_id,
                sequence=work_unit_sequence,
                event_type=work_unit_event_type,
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload={
                    "previousStatus": work_unit.status.value,
                    "status": updated_work_unit.status.value,
                    "evidenceId": evidence.id,
                    "verdict": verdict.value,
                },
                schema_version=1,
            )
            await repository.append_event(work_unit_event)

            updated_mission = mission
            if verdict == EvidenceVerdict.FAIL:
                updated_mission = await self._fail_mission_for_work_unit(
                    repository,
                    mission,
                    work_unit_id=work_unit.id,
                    actor=actor,
                    reason=summary,
                    occurred_at=occurred_at,
                    causation_id=work_unit_event.event_id,
                )
            elif verdict == EvidenceVerdict.PASS:
                work_units = await repository.list_work_units(mission_id)
                passed_criteria = await repository.list_passed_evidence_criterion_ids(
                    mission_id
                )
                required_criteria = {
                    criterion.id
                    for criterion in contract.acceptance_criteria
                    if criterion.required
                }
                if (
                    work_units
                    and all(
                        item.status == WorkUnitStatus.SUCCEEDED for item in work_units
                    )
                    and required_criteria <= passed_criteria
                ):
                    mission_sequence = await repository.get_last_event_sequence(
                        mission_id
                    )
                    if mission.status == MissionStatus.RUNNING:
                        updated_mission = transition_mission(
                            mission,
                            MissionStatus.VERIFYING,
                            occurred_at=occurred_at,
                        )
                        mission_sequence += 1
                        await repository.append_event(
                            EventEnvelope(
                                event_id=new_identifier("evt"),
                                aggregate_type="mission",
                                aggregate_id=mission_id,
                                sequence=mission_sequence,
                                event_type="mission.lifecycle.verifying",
                                actor=actor,
                                occurred_at=occurred_at,
                                correlation_id=mission_id,
                                payload={
                                    "previousStatus": mission.status.value,
                                    "status": updated_mission.status.value,
                                },
                                schema_version=1,
                            )
                        )
                    updated_mission = transition_mission(
                        updated_mission,
                        MissionStatus.SUCCEEDED,
                        occurred_at=occurred_at,
                    )
                    mission_sequence += 1
                    await repository.append_event(
                        EventEnvelope(
                            event_id=new_identifier("evt"),
                            aggregate_type="mission",
                            aggregate_id=mission_id,
                            sequence=mission_sequence,
                            event_type="mission.lifecycle.succeeded",
                            actor=actor,
                            occurred_at=occurred_at,
                            correlation_id=mission_id,
                            payload={
                                "previousStatus": (
                                    MissionStatus.VERIFYING.value
                                    if mission.status == MissionStatus.RUNNING
                                    else mission.status.value
                                ),
                                "status": updated_mission.status.value,
                            },
                            schema_version=1,
                        )
                    )
                    await repository.update_mission(updated_mission)

            return evidence, updated_work_unit, updated_mission

    def _admit_pass_evidence(
        self,
        *,
        contract: MissionContract,
        work_unit: WorkUnit,
        artifacts: tuple[Artifact, ...],
        criterion_id: str,
        configuration_digest: str | None,
    ) -> ArtifactSetEvaluationPlan:
        decision = self._verification_policy_resolver.resolve(
            contract,
            work_unit,
            artifacts,
        )
        if decision.plan is None:
            assert decision.reason is not None
            raise WorkUnitNotReadyError(
                "PASS evidence is not admitted by the evaluation policy: "
                f"{decision.reason.value}"
            )
        if criterion_id != decision.plan.criterion_id:
            raise WorkUnitNotReadyError(
                "Evidence criterion does not match the evaluation policy"
            )
        if configuration_digest is None:
            raise WorkUnitNotReadyError(
                "PASS evidence requires the evaluation policy configuration digest"
            )
        if configuration_digest != decision.plan.configuration_digest:
            raise WorkUnitNotReadyError(
                "Evidence configuration digest does not match the evaluation policy"
            )
        return decision.plan

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
