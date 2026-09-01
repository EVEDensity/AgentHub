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


class MissionVerifyMixin:
    """Mixin holding MissionService verification execution methods."""

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
