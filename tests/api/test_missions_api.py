from __future__ import annotations

import unittest
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.missions import (
    get_agent_binding_resolver,
    get_artifact_byte_verifier,
    get_mission_repository,
    get_runner_workspace_grant_authorizer,
    get_verifier_workspace_grant_authorizer,
    get_workspace_claim_admission_policy_resolver,
    router,
)
from app.domain import (
    ActorRef,
    Artifact,
    Decision,
    DecisionStatus,
    EvaluationPolicyReason,
    EventEnvelope,
    Evidence,
    ExecutionCheckpoint,
    Lease,
    Mission,
    MissionContract,
    MissionSource,
    WorkUnit,
)
from app.services.agent_binding_service import (
    AgentBinding,
    AgentBindingResolver,
    DatabaseAgentBindingResolver,
    StaticAgentBindingResolver,
    UnavailableAgentBindingResolver,
)
from app.services.artifact_integrity_service import (
    ArtifactBytesUnavailableError,
    ArtifactByteVerification,
    ArtifactByteVerificationError,
    ArtifactIntegrityError,
)
from app.services.auth_service import get_current_user
from app.services.evidence_integrity_service import (
    EvidenceIntegrityMaterial,
    Sha256EvidenceIntegrityHasher,
)
from app.services.mission_service import MissionService
from app.services.verification_evaluator_service import StrictVerificationEvaluator
from app.services.verification_policy_service import StrictVerificationPolicyResolver
from app.services.workspace_access_service import (
    DatabaseRunnerWorkspaceGrantAuthorizer,
    DatabaseVerifierWorkspaceGrantAuthorizer,
    RunnerWorkspaceGrantAuthorizer,
    RunnerWorkspaceGrantUnavailableError,
    VerifierWorkspaceGrantAuthorizer,
    VerifierWorkspaceGrantUnavailableError,
)
from app.services.workspace_admission_service import (
    DatabaseWorkspaceClaimAdmissionPolicyResolver,
    WorkspaceClaimAdmissionDeniedError,
    WorkspaceClaimAdmissionPolicy,
    WorkspaceClaimAdmissionPolicyResolver,
    WorkspaceClaimAdmissionUnavailableError,
)
from tests.domain.factories import (
    DIGEST,
    build_artifact,
    build_contract,
    build_decision,
    build_event,
    build_evidence,
    build_execution_checkpoint,
    build_mission,
    build_work_unit,
)


class FakeMissionRepository:
    def __init__(self) -> None:
        self.contract: MissionContract | None = None
        self.contracts: list[MissionContract] = []
        self.mission: Mission | None = None
        self.events: list[EventEnvelope] = []
        self.artifacts: list[Artifact] = []
        self.evidence: list[Evidence] = []
        self.execution_checkpoints: list[ExecutionCheckpoint] = []
        self.decisions: list[Decision] = []
        self.list_result: list[Mission] = []
        self.work_units: list[WorkUnit] = []
        self.transaction_depth = 0
        self.admission_locks: list[str] = []
        self.contract_lineage_locks: list[str] = []
        self.contract_lineage_workspaces: dict[str, str] = {}
        self.tenant_active_count_override: int | None = None
        self.verification_candidate_calls: list[str] = []
        self.work_unit_artifact_calls: list[tuple[str, str, int, int]] = []
        self.list_mission_calls: list[str] = []
        self.workspace_decision_calls: list[tuple[object, ...]] = []

    @asynccontextmanager
    async def transaction(self):
        self.transaction_depth += 1
        try:
            yield self
        finally:
            self.transaction_depth -= 1

    async def lock_tenant_claim_admission(self, tenant_id: str) -> None:
        if not self.transaction_depth:
            raise AssertionError("admission lock requires a transaction")
        self.admission_locks.append(tenant_id)

    async def count_tenant_active_runner_work_units(self, tenant_id: str) -> int:
        del tenant_id
        if not self.transaction_depth:
            raise AssertionError("admission count requires a transaction")
        if self.tenant_active_count_override is not None:
            return self.tenant_active_count_override
        now = datetime.now(timezone.utc)
        return sum(
            work_unit.status.value in {"LEASED", "RUNNING"}
            and work_unit.lease is not None
            and work_unit.lease.expires_at > now
            for work_unit in self.work_units
        )

    async def add_contract(self, contract: MissionContract) -> None:
        self.contract = contract
        self.contracts.append(contract)

    async def get_contract(
        self,
        contract_id: str,
        contract_version: int,
    ) -> MissionContract | None:
        candidates = [*self.contracts]
        if self.contract is not None and self.contract not in candidates:
            candidates.append(self.contract)
        return next(
            (
                contract
                for contract in candidates
                if contract.id == contract_id
                and contract.version == contract_version
            ),
            None,
        )

    async def lock_contract_lineage(self, contract_id: str) -> None:
        if not self.transaction_depth:
            raise AssertionError("Contract lineage lock requires a transaction")
        self.contract_lineage_locks.append(contract_id)

    async def get_latest_contract(self, contract_id: str) -> MissionContract | None:
        candidates = [
            contract
            for contract in self.contracts
            if contract.id == contract_id
        ]
        if (
            self.contract is not None
            and self.contract.id == contract_id
            and self.contract not in candidates
        ):
            candidates.append(self.contract)
        return max(candidates, key=lambda contract: contract.version, default=None)

    async def add_contract_lineage(
        self,
        contract_id: str,
        workspace_id: str,
    ) -> None:
        self.contract_lineage_workspaces[contract_id] = workspace_id

    async def get_contract_lineage_workspace(
        self,
        contract_id: str,
    ) -> str | None:
        return self.contract_lineage_workspaces.get(contract_id)

    async def add_mission(self, mission: Mission) -> None:
        self.mission = mission

    async def get_mission(self, mission_id: str) -> Mission | None:
        if self.mission and self.mission.id == mission_id:
            return self.mission
        return None

    async def get_mission_by_source(
        self,
        workspace_id: str,
        *,
        source_type: str,
        external_id: str,
        source_reference: str | None = None,
    ) -> Mission | None:
        mission = self.mission
        if (
            mission is not None
            and mission.workspace_id == workspace_id
            and mission.source.type.value == source_type
            and mission.source.external_id == external_id
            and (
                source_reference is None or mission.source.reference == source_reference
            )
        ):
            return mission
        return None

    async def get_mission_for_update(self, mission_id: str) -> Mission | None:
        return await self.get_mission(mission_id)

    async def update_mission(self, mission: Mission) -> None:
        self.mission = mission

    async def get_last_event_sequence(
        self,
        aggregate_id: str,
        *,
        aggregate_type: str = "mission",
    ) -> int:
        sequences = [
            event.sequence
            for event in self.events
            if event.aggregate_type.value == aggregate_type
            and event.aggregate_id == aggregate_id
        ]
        return max(sequences, default=0)

    async def list_events(
        self,
        mission_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[EventEnvelope]:
        events = sorted(
            (
                event
                for event in self.events
                if event.aggregate_type.value == "mission"
                and event.aggregate_id == mission_id
                and event.sequence > after_sequence
            ),
            key=lambda event: event.sequence,
        )
        return events[:limit]

    async def add_evidence(self, evidence: Evidence) -> None:
        self.evidence.append(evidence)

    async def get_evidence(self, evidence_id: str) -> Evidence | None:
        return next(
            (evidence for evidence in self.evidence if evidence.id == evidence_id),
            None,
        )

    async def list_evidence(
        self,
        mission_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Evidence]:
        matching = sorted(
            (
                evidence
                for evidence in self.evidence
                if evidence.mission_id == mission_id
            ),
            key=lambda evidence: (evidence.generated_at, evidence.id),
        )
        return matching[offset : offset + limit]

    async def list_passed_evidence_criterion_ids(self, mission_id: str) -> set[str]:
        return {
            evidence.criterion_id
            for evidence in self.evidence
            if evidence.mission_id == mission_id and evidence.verdict.value == "PASS"
        }

    async def add_decision(self, decision: Decision) -> None:
        if any(existing.id == decision.id for existing in self.decisions):
            raise ValueError("decision id already exists")
        if any(
            existing.work_unit_id == decision.work_unit_id
            and existing.attempt == decision.attempt
            and existing.context_digest == decision.context_digest
            for existing in self.decisions
        ):
            raise ValueError("decision context already exists")
        self.decisions.append(decision)

    async def get_decision(self, decision_id: str) -> Decision | None:
        return next(
            (decision for decision in self.decisions if decision.id == decision_id),
            None,
        )

    async def get_decision_for_update(self, decision_id: str) -> Decision | None:
        if not self.transaction_depth:
            raise AssertionError("decision lock requires a transaction")
        return await self.get_decision(decision_id)

    async def list_decisions(
        self,
        mission_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Decision]:
        matching = sorted(
            (
                decision
                for decision in self.decisions
                if decision.mission_id == mission_id
            ),
            key=lambda decision: (decision.requested_at, decision.id),
        )
        return matching[offset : offset + limit]

    async def list_workspace_decisions(
        self,
        workspace_id: str,
        *,
        status: DecisionStatus | None = DecisionStatus.PENDING,
        mission_id: str | None = None,
        reason_code: EvaluationPolicyReason | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Decision]:
        self.workspace_decision_calls.append(
            (workspace_id, status, mission_id, reason_code, limit, offset)
        )
        workspace_mission_ids = {
            mission.id
            for mission in [self.mission, *self.list_result]
            if mission is not None and mission.workspace_id == workspace_id
        }
        matching = sorted(
            (
                decision
                for decision in self.decisions
                if decision.mission_id in workspace_mission_ids
                and (status is None or decision.status == status)
                and (mission_id is None or decision.mission_id == mission_id)
                and (reason_code is None or decision.reason_code == reason_code)
            ),
            key=lambda decision: (decision.requested_at, decision.id),
        )
        return matching[offset : offset + limit]

    async def get_expired_decision_candidate_for_update(
        self,
        occurred_at: datetime,
    ) -> tuple[Mission, Decision] | None:
        if not self.transaction_depth:
            raise AssertionError("expired decision selection requires a transaction")
        if self.mission is None or self.mission.status.value != "WAITING_DECISION":
            return None
        candidates = sorted(
            (
                decision
                for decision in self.decisions
                if decision.mission_id == self.mission.id
                and decision.status == DecisionStatus.PENDING
                and decision.expires_at is not None
                and decision.expires_at <= occurred_at
            ),
            key=lambda decision: (decision.expires_at, decision.id),
        )
        return (self.mission, candidates[0]) if candidates else None

    async def list_pending_decisions_for_update(
        self,
        mission_id: str,
    ) -> list[Decision]:
        if not self.transaction_depth:
            raise AssertionError("pending decision lock requires a transaction")
        return sorted(
            (
                decision
                for decision in self.decisions
                if decision.mission_id == mission_id
                and decision.status.value == "PENDING"
            ),
            key=lambda decision: (decision.requested_at, decision.id),
        )

    async def update_decision(self, decision: Decision) -> None:
        for index, existing in enumerate(self.decisions):
            if existing.id == decision.id:
                self.decisions[index] = decision
                return
        raise AssertionError("decision update requires an existing row")

    async def add_artifact(self, artifact: Artifact) -> None:
        self.artifacts.append(artifact)

    async def add_execution_checkpoint(
        self,
        checkpoint: ExecutionCheckpoint,
    ) -> None:
        self.execution_checkpoints.append(checkpoint)

    async def get_execution_checkpoint(
        self,
        checkpoint_id: str,
    ) -> ExecutionCheckpoint | None:
        return next(
            (
                checkpoint
                for checkpoint in self.execution_checkpoints
                if checkpoint.id == checkpoint_id
            ),
            None,
        )

    async def get_latest_execution_checkpoint(
        self,
        work_unit_id: str,
        attempt: int,
    ) -> ExecutionCheckpoint | None:
        candidates = [
            checkpoint
            for checkpoint in self.execution_checkpoints
            if checkpoint.work_unit_id == work_unit_id
            and checkpoint.attempt == attempt
        ]
        return max(candidates, key=lambda item: item.sequence, default=None)

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        return next(
            (artifact for artifact in self.artifacts if artifact.id == artifact_id),
            None,
        )

    async def list_artifacts(
        self,
        mission_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]:
        matching = sorted(
            (
                artifact
                for artifact in self.artifacts
                if artifact.mission_id == mission_id
            ),
            key=lambda artifact: (artifact.created_at, artifact.id),
        )
        return matching[offset : offset + limit]

    async def list_work_unit_artifacts(
        self,
        mission_id: str,
        work_unit_id: str,
        attempt: int,
        *,
        limit: int = 201,
    ) -> list[Artifact]:
        self.work_unit_artifact_calls.append((mission_id, work_unit_id, attempt, limit))
        matching = sorted(
            (
                artifact
                for artifact in self.artifacts
                if artifact.mission_id == mission_id
                and artifact.work_unit_id == work_unit_id
                and artifact.attempt == attempt
            ),
            key=lambda artifact: (artifact.created_at, artifact.id),
        )
        return matching[:limit]

    async def list_missions(
        self,
        workspace_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Mission]:
        self.list_mission_calls.append(workspace_id)
        return [
            mission
            for mission in self.list_result
            if mission.workspace_id == workspace_id
        ][offset : offset + limit]

    async def add_work_unit(self, work_unit: WorkUnit) -> None:
        self.work_units.append(work_unit)

    async def get_work_unit(self, work_unit_id: str) -> WorkUnit | None:
        return next(
            (
                work_unit
                for work_unit in self.work_units
                if work_unit.id == work_unit_id
            ),
            None,
        )

    async def get_work_unit_for_update(self, work_unit_id: str) -> WorkUnit | None:
        return await self.get_work_unit(work_unit_id)

    async def get_workspace_verification_candidate(
        self,
        workspace_id: str,
    ) -> tuple[Mission, WorkUnit] | None:
        if not self.transaction_depth:
            raise AssertionError("verification discovery requires a transaction")
        self.verification_candidate_calls.append(workspace_id)
        mission = self.mission
        if (
            mission is None
            or mission.workspace_id != workspace_id
            or mission.status.value not in {"RUNNING", "VERIFYING"}
        ):
            return None
        candidates = sorted(
            (
                work_unit
                for work_unit in self.work_units
                if work_unit.mission_id == mission.id
                and work_unit.status.value == "VERIFYING"
            ),
            key=lambda work_unit: work_unit.id,
        )
        return (mission, candidates[0]) if candidates else None

    async def get_bound_work_unit_for_claim(
        self,
        mission_id: str,
        *,
        agent_id: str,
        adapter_type: str,
        allowed_root_kind: str | None,
    ) -> WorkUnit | None:
        def root_is_eligible(work_unit: WorkUnit) -> bool:
            if work_unit.parent_work_unit_id is not None:
                return True
            if work_unit.kind != allowed_root_kind:
                return False
            return (
                allowed_root_kind in {"a2a.inbound", "mission.fork"}
                and work_unit.assigned_adapter != "a2a.outbound"
            ) or (
                allowed_root_kind == "a2a.delegate"
                and work_unit.assigned_adapter == "a2a.outbound"
            )

        candidates = sorted(
            (
                work_unit
                for work_unit in self.work_units
                if work_unit.mission_id == mission_id
                and root_is_eligible(work_unit)
                and work_unit.assigned_agent_id == agent_id
                and work_unit.assigned_adapter == adapter_type
                and work_unit.status.value in {"PENDING", "RETRYING"}
                and all(
                    (
                        dependency := next(
                            (
                                item
                                for item in self.work_units
                                if item.id == dependency_id
                            ),
                            None,
                        )
                    )
                    is not None
                    and dependency.mission_id == mission_id
                    and dependency.status.value == "SUCCEEDED"
                    for dependency_id in work_unit.dependencies
                )
            ),
            key=lambda work_unit: work_unit.id,
        )
        return candidates[0] if candidates else None

    async def get_workspace_bound_work_unit_for_claim(
        self,
        workspace_id: str,
        *,
        agent_id: str,
        adapter_type: str,
    ) -> tuple[Mission, WorkUnit] | None:
        missions = {
            mission.id: mission
            for mission in ([self.mission] if self.mission is not None else [])
            + self.list_result
        }
        selections: list[tuple[int, object, str, str, Mission, WorkUnit]] = []
        for work_unit in self.work_units:
            mission = missions.get(work_unit.mission_id)
            if (
                mission is None
                or mission.workspace_id != workspace_id
                or mission.status.value != "RUNNING"
                or work_unit.assigned_agent_id != agent_id
                or work_unit.assigned_adapter != adapter_type
                or work_unit.status.value not in {"PENDING", "RETRYING"}
            ):
                continue
            eligible_shape = (
                work_unit.parent_work_unit_id is not None
                or (
                    mission.source.type.value == "a2a.inbound"
                    and work_unit.parent_work_unit_id is None
                    and work_unit.kind == "a2a.inbound"
                    and work_unit.assigned_adapter != "a2a.outbound"
                )
                or (
                    mission.source.type.value == "a2a"
                    and work_unit.parent_work_unit_id is None
                    and work_unit.kind == "a2a.delegate"
                    and work_unit.assigned_adapter == "a2a.outbound"
                )
                or (
                    mission.source.type.value == "mission.fork"
                    and work_unit.parent_work_unit_id is None
                    and work_unit.kind == "mission.fork"
                    and work_unit.assigned_adapter != "a2a.outbound"
                )
            )
            if not eligible_shape:
                continue
            dependencies_ready = all(
                (
                    dependency := next(
                        (item for item in self.work_units if item.id == dependency_id),
                        None,
                    )
                )
                is not None
                and dependency.mission_id == mission.id
                and dependency.status.value == "SUCCEEDED"
                for dependency_id in work_unit.dependencies
            )
            if not dependencies_ready:
                continue
            active_count = sum(
                item.mission_id == mission.id
                and item.status.value in {"LEASED", "RUNNING", "VERIFYING"}
                for item in self.work_units
            )
            selections.append(
                (
                    active_count,
                    mission.created_at,
                    mission.id,
                    work_unit.id,
                    mission,
                    work_unit,
                )
            )
        if not selections:
            return None
        *_, mission, work_unit = min(selections)
        return mission, work_unit

    async def update_work_unit(self, work_unit: WorkUnit) -> None:
        for index, existing in enumerate(self.work_units):
            if existing.id == work_unit.id:
                self.work_units[index] = work_unit
                return
        self.work_units.append(work_unit)

    async def list_work_units(
        self,
        mission_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkUnit]:
        matching = [
            work_unit
            for work_unit in self.work_units
            if work_unit.mission_id == mission_id
        ]
        return sorted(matching, key=lambda work_unit: work_unit.id)[
            offset : offset + limit
        ]

    async def list_work_units_for_update(self, mission_id: str) -> list[WorkUnit]:
        return sorted(
            (
                work_unit
                for work_unit in self.work_units
                if work_unit.mission_id == mission_id
            ),
            key=lambda work_unit: work_unit.id,
        )

    async def append_event(self, event: EventEnvelope) -> None:
        self.events.append(event)


class FakeRunnerWorkspaceGrantAuthorizer:
    def __init__(
        self,
        grants: set[tuple[str, str]] | None = None,
        *,
        error: RunnerWorkspaceGrantUnavailableError | None = None,
    ) -> None:
        self.grants = grants or set()
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def has_claim_grant(
        self,
        *,
        workspace_id: str,
        principal_id: str,
    ) -> bool:
        self.calls.append((workspace_id, principal_id))
        if self.error is not None:
            raise self.error
        return (workspace_id, principal_id) in self.grants


class FakeVerifierWorkspaceGrantAuthorizer:
    def __init__(
        self,
        grants: set[tuple[str, str]] | None = None,
        *,
        error: VerifierWorkspaceGrantUnavailableError | None = None,
    ) -> None:
        self.grants = grants or set()
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def has_verify_grant(
        self,
        *,
        workspace_id: str,
        principal_id: str,
    ) -> bool:
        self.calls.append((workspace_id, principal_id))
        if self.error is not None:
            raise self.error
        return (workspace_id, principal_id) in self.grants


class FakeWorkspaceClaimAdmissionPolicyResolver:
    def __init__(
        self,
        policy: WorkspaceClaimAdmissionPolicy | None = None,
        *,
        error: RuntimeError | None = None,
    ) -> None:
        self.policy = policy or WorkspaceClaimAdmissionPolicy(
            tenant_id="tenant-test",
            max_concurrent=0,
        )
        self.error = error
        self.calls: list[str] = []

    async def resolve(
        self,
        *,
        workspace_id: str,
    ) -> WorkspaceClaimAdmissionPolicy:
        self.calls.append(workspace_id)
        if self.error is not None:
            raise self.error
        return self.policy


class FakeArtifactByteVerifier:
    def __init__(
        self,
        *,
        error: ArtifactByteVerificationError | None = None,
        on_verify: Callable[[list[Artifact]], None] | None = None,
        results: list[ArtifactByteVerification] | None = None,
    ) -> None:
        self.error = error
        self.on_verify = on_verify
        self.results = results
        self.calls: list[list[Artifact]] = []
        self.repository: FakeMissionRepository | None = None

    async def verify_all(
        self,
        artifacts: list[Artifact],
    ) -> list[ArtifactByteVerification]:
        if self.repository is not None and self.repository.transaction_depth:
            raise AssertionError("artifact storage I/O ran inside a transaction")
        self.calls.append(list(artifacts))
        if self.on_verify is not None:
            self.on_verify(artifacts)
        if self.error is not None:
            raise self.error
        if self.results is not None:
            return list(self.results)
        return [
            ArtifactByteVerification(
                artifact_id=artifact.id,
                digest=artifact.digest,
                size_bytes=artifact.size_bytes,
            )
            for artifact in artifacts
        ]


class ForkMissionRepository(FakeMissionRepository):
    """Multi-Mission fake scoped to the fork command's ancestry behavior."""

    def __init__(self) -> None:
        super().__init__()
        source = build_mission(status="SUCCEEDED")
        self.missions = {source.id: source}
        self.contract = build_contract()
        self.contracts = [self.contract]
        self.contract_lineage_workspaces = {self.contract.id: source.workspace_id}
        self.work_units = [build_work_unit(status="SUCCEEDED", attempt=1)]
        self.execution_checkpoints = [
            build_execution_checkpoint(
                sequence=5,
                phase="harness.execution.completed",
                terminal=True,
            )
        ]
        self.artifacts = [build_artifact()]

    async def add_mission(self, mission: Mission) -> None:
        if mission.id in self.missions:
            raise ValueError("Mission already exists")
        self.missions[mission.id] = mission

    async def get_mission(self, mission_id: str) -> Mission | None:
        return self.missions.get(mission_id)

    async def get_mission_for_update(self, mission_id: str) -> Mission | None:
        return self.missions.get(mission_id)

    async def update_mission(self, mission: Mission) -> None:
        if mission.id not in self.missions:
            raise AssertionError("Mission update requires an existing row")
        self.missions[mission.id] = mission


def mission_fork_request(**updates: object) -> dict[str, object]:
    request: dict[str, object] = {
        "id": "mis-fork",
        "workUnitId": "wu-fork",
        "title": "Continue from verified output",
        "objective": "Use verified artifacts as bounded input.",
        "checkpointId": "chk-1",
        "artifactRefs": [{"id": "artifact-1", "digest": DIGEST}],
        "expectedOutputs": [{"kind": "report", "required": True}],
        "requiredCapabilities": ["repository.write"],
        "agentId": "reviewer",
    }
    request.update(updates)
    return request


def mission_fork_binding_resolver() -> StaticAgentBindingResolver:
    return StaticAgentBindingResolver(
        {
            ("workspace-1", "reviewer"): AgentBinding(
                agent_id="reviewer",
                adapter_type="local_codex",
                capabilities=("repository.write",),
            )
        }
    )


def artifact_set_criterion(
    *,
    criterion_id: str = "tests",
    criterion_kind: str = "test",
    work_unit_kind: str = "code_change",
    required_artifact_kinds: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": criterion_id,
        "kind": criterion_kind,
        "description": f"{criterion_id} artifacts satisfy the contract",
        "required": True,
        "configuration": {
            "evaluator": "artifact-set.v1",
            "workUnitKinds": [work_unit_kind],
            "minimumArtifacts": 1,
            "requiredArtifactKinds": required_artifact_kinds or [],
        },
    }


def evaluation_policy_digest(
    contract: MissionContract,
    work_unit: WorkUnit,
    artifacts: list[Artifact],
) -> str:
    decision = StrictVerificationPolicyResolver().resolve(
        contract,
        work_unit,
        tuple(artifacts),
    )
    if decision.plan is None:
        raise AssertionError(f"test evaluation policy is not ready: {decision.reason}")
    return decision.plan.configuration_digest


def recompute_evidence_integrity_hash(
    repository: FakeMissionRepository,
    evidence: Evidence,
) -> str:
    if repository.mission is None or repository.contract is None:
        raise AssertionError("test Mission and Contract must be configured")
    work_unit = next(
        item for item in repository.work_units if item.id == evidence.work_unit_id
    )
    artifacts_by_id = {artifact.id: artifact for artifact in repository.artifacts}
    artifacts = tuple(artifacts_by_id[ref.id] for ref in evidence.artifact_refs)
    observations = tuple(
        ArtifactByteVerification(
            artifact_id=artifact.id,
            digest=artifact.digest,
            size_bytes=artifact.size_bytes,
        )
        for artifact in artifacts
    )
    evaluation = None
    if evidence.verdict.value == "PASS":
        decision = StrictVerificationPolicyResolver().resolve(
            repository.contract,
            work_unit,
            artifacts,
        )
        if decision.plan is None:
            raise AssertionError("test PASS policy must resolve")
        evaluation = StrictVerificationEvaluator().evaluate(
            decision.plan,
            artifacts,
            observations,
        )
    return Sha256EvidenceIntegrityHasher().compute(
        EvidenceIntegrityMaterial(
            evidence_id=evidence.id,
            mission_id=evidence.mission_id,
            contract_id=repository.contract.id,
            contract_version=repository.contract.version,
            work_unit_id=work_unit.id,
            work_unit_attempt=work_unit.attempt,
            criterion_id=evidence.criterion_id,
            verifier=evidence.verifier,
            verdict=evidence.verdict,
            artifact_refs=evidence.artifact_refs,
            artifacts=artifacts,
            byte_verifications=observations,
            evaluation=evaluation,
            summary=evidence.summary,
            generated_at=evidence.generated_at,
        )
    )


def build_app(
    repository: FakeMissionRepository,
    user: dict[str, Any],
    *,
    artifact_byte_verifier: FakeArtifactByteVerifier | None = None,
    agent_binding_resolver: AgentBindingResolver | None = None,
    runner_workspace_grant_authorizer: RunnerWorkspaceGrantAuthorizer | None = None,
    verifier_workspace_grant_authorizer: (
        VerifierWorkspaceGrantAuthorizer | None
    ) = None,
    workspace_claim_admission_policy_resolver: (
        WorkspaceClaimAdmissionPolicyResolver | None
    ) = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    verifier = artifact_byte_verifier or FakeArtifactByteVerifier()
    verifier.repository = repository
    app.dependency_overrides[get_mission_repository] = lambda: repository
    app.dependency_overrides[get_artifact_byte_verifier] = lambda: verifier
    binding_resolver = agent_binding_resolver or UnavailableAgentBindingResolver()
    app.dependency_overrides[get_agent_binding_resolver] = lambda: binding_resolver
    grant_authorizer = (
        runner_workspace_grant_authorizer or FakeRunnerWorkspaceGrantAuthorizer()
    )
    app.dependency_overrides[get_runner_workspace_grant_authorizer] = lambda: (
        grant_authorizer
    )
    verifier_grant_authorizer = (
        verifier_workspace_grant_authorizer or FakeVerifierWorkspaceGrantAuthorizer()
    )
    app.dependency_overrides[get_verifier_workspace_grant_authorizer] = lambda: (
        verifier_grant_authorizer
    )
    admission_policy_resolver = (
        workspace_claim_admission_policy_resolver
        or FakeWorkspaceClaimAdmissionPolicyResolver()
    )
    app.dependency_overrides[get_workspace_claim_admission_policy_resolver] = lambda: (
        admission_policy_resolver
    )
    app.dependency_overrides[get_current_user] = lambda: user
    return app


class MissionApiTests(unittest.TestCase):
    def test_default_agent_binding_dependency_uses_durable_catalog(self) -> None:
        self.assertIsInstance(
            get_agent_binding_resolver(),
            DatabaseAgentBindingResolver,
        )

    def test_human_can_fork_verified_mission_idempotently(self) -> None:
        repository = ForkMissionRepository()
        verifier = FakeArtifactByteVerifier()
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
                artifact_byte_verifier=verifier,
                agent_binding_resolver=mission_fork_binding_resolver(),
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/forks",
            json=mission_fork_request(),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["mission"]["id"], "mis-fork")
        self.assertEqual(response.json()["mission"]["source"]["type"], "mission.fork")
        self.assertEqual(response.json()["workUnit"]["id"], "wu-fork")
        self.assertEqual(response.json()["workUnit"]["status"], "PENDING")
        self.assertEqual(len(repository.events), 2)
        self.assertEqual(len(verifier.calls), 1)

        replay = client.post(
            "/api/v1/missions/mis-1/forks",
            json=mission_fork_request(),
        )

        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json(), response.json())
        self.assertEqual(len(repository.events), 2)
        self.assertEqual(len(verifier.calls), 1)

    def test_non_human_roles_cannot_fork_before_artifact_io(self) -> None:
        for role in ("runner", "verifier", "agent", "service"):
            with self.subTest(role=role):
                repository = ForkMissionRepository()
                verifier = FakeArtifactByteVerifier()
                client = TestClient(
                    build_app(
                        repository,
                        {"id": "workspace-1", "name": role, "role": role},
                        artifact_byte_verifier=verifier,
                        agent_binding_resolver=mission_fork_binding_resolver(),
                    )
                )

                response = client.post(
                    "/api/v1/missions/mis-1/forks",
                    json=mission_fork_request(),
                )

                self.assertEqual(response.status_code, 403)
                self.assertEqual(verifier.calls, [])
                self.assertEqual(repository.events, [])

    def test_fork_requires_source_workspace_access_before_artifact_io(self) -> None:
        repository = ForkMissionRepository()
        verifier = FakeArtifactByteVerifier()
        client = TestClient(
            build_app(
                repository,
                {"id": "other-workspace", "name": "Ada", "role": "developer"},
                artifact_byte_verifier=verifier,
                agent_binding_resolver=mission_fork_binding_resolver(),
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/forks",
            json=mission_fork_request(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(verifier.calls, [])
        self.assertEqual(repository.events, [])

    def test_fork_rejects_invalid_source_before_artifact_io(self) -> None:
        repository = ForkMissionRepository()
        repository.execution_checkpoints[0] = build_execution_checkpoint(
            phase="harness.model.completed",
            terminal=False,
        )
        verifier = FakeArtifactByteVerifier()
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
                artifact_byte_verifier=verifier,
                agent_binding_resolver=mission_fork_binding_resolver(),
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/forks",
            json=mission_fork_request(),
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("successful terminal checkpoint", response.json()["detail"])
        self.assertEqual(verifier.calls, [])
        self.assertEqual(repository.events, [])

    def test_fork_maps_catalog_failures_without_artifact_io(self) -> None:
        cases = (
            (UnavailableAgentBindingResolver(), 503, "not configured"),
            (StaticAgentBindingResolver({}), 409, "not available"),
            (
                StaticAgentBindingResolver(
                    {
                        ("workspace-1", "reviewer"): AgentBinding(
                            agent_id="reviewer",
                            adapter_type="a2a.outbound",
                            capabilities=("repository.write",),
                        )
                    }
                ),
                409,
                "non-outbound execution adapter",
            ),
        )
        for resolver, expected_status, detail in cases:
            with self.subTest(expected_status=expected_status):
                repository = ForkMissionRepository()
                verifier = FakeArtifactByteVerifier()
                client = TestClient(
                    build_app(
                        repository,
                        {
                            "id": "workspace-1",
                            "name": "Ada",
                            "role": "developer",
                        },
                        artifact_byte_verifier=verifier,
                        agent_binding_resolver=resolver,
                    )
                )

                response = client.post(
                    "/api/v1/missions/mis-1/forks",
                    json=mission_fork_request(),
                )

                self.assertEqual(response.status_code, expected_status)
                self.assertIn(detail, response.json()["detail"])
                self.assertEqual(verifier.calls, [])
                self.assertEqual(repository.events, [])

    def test_fork_maps_artifact_storage_failures(self) -> None:
        cases = (
            (ArtifactBytesUnavailableError("artifact store unavailable"), 424),
            (ArtifactIntegrityError("artifact byte digest does not match"), 409),
        )
        for error, expected_status in cases:
            with self.subTest(expected_status=expected_status):
                repository = ForkMissionRepository()
                verifier = FakeArtifactByteVerifier(error=error)
                client = TestClient(
                    build_app(
                        repository,
                        {
                            "id": "workspace-1",
                            "name": "Ada",
                            "role": "developer",
                        },
                        artifact_byte_verifier=verifier,
                        agent_binding_resolver=mission_fork_binding_resolver(),
                    )
                )

                response = client.post(
                    "/api/v1/missions/mis-1/forks",
                    json=mission_fork_request(),
                )

                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["detail"], str(error))
                self.assertEqual(len(verifier.calls), 1)
                self.assertEqual(repository.events, [])

    def test_fork_request_rejects_empty_artifacts_and_unknown_fields(self) -> None:
        repository = ForkMissionRepository()
        verifier = FakeArtifactByteVerifier()
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
                artifact_byte_verifier=verifier,
                agent_binding_resolver=mission_fork_binding_resolver(),
            )
        )

        for request in (
            mission_fork_request(artifactRefs=[]),
            mission_fork_request(workspaceId="forged-workspace"),
        ):
            with self.subTest(request=request):
                response = client.post(
                    "/api/v1/missions/mis-1/forks",
                    json=request,
                )
                self.assertEqual(response.status_code, 422)

        self.assertEqual(verifier.calls, [])
        self.assertEqual(repository.events, [])

    def test_default_runner_grant_dependency_uses_workspace_acl(self) -> None:
        self.assertIsInstance(
            get_runner_workspace_grant_authorizer(),
            DatabaseRunnerWorkspaceGrantAuthorizer,
        )

    def test_default_verifier_grant_dependency_uses_workspace_acl(self) -> None:
        self.assertIsInstance(
            get_verifier_workspace_grant_authorizer(),
            DatabaseVerifierWorkspaceGrantAuthorizer,
        )

    def test_default_workspace_admission_dependency_uses_iam_quota(self) -> None:
        self.assertIsInstance(
            get_workspace_claim_admission_policy_resolver(),
            DatabaseWorkspaceClaimAdmissionPolicyResolver,
        )

    def test_create_mission_derives_actor_and_appends_first_event(self) -> None:
        repository = FakeMissionRepository()
        user = {"id": "user-1", "name": "Ada", "role": "developer"}
        client = TestClient(build_app(repository, user))
        legacy_contract = build_contract().to_public_dict()
        legacy_contract.pop("governance")

        response = client.post(
            "/api/v1/missions",
            json={
                "id": "mis-api-1",
                "workspaceId": "user-1",
                "title": "Ship Mission API",
                "objective": "Create the first Mission endpoint.",
                "source": {"type": "api", "reference": "local-test"},
                "contract": legacy_contract,
                "createdBy": {"type": "human", "id": "forged"},
            },
        )

        self.assertEqual(response.status_code, 422)

        valid_response = client.post(
            "/api/v1/missions",
            json={
                "id": "mis-api-1",
                "workspaceId": "user-1",
                "title": "Ship Mission API",
                "objective": "Create the first Mission endpoint.",
                "source": {"type": "api", "reference": "local-test"},
                "contract": legacy_contract,
            },
        )

        self.assertEqual(valid_response.status_code, 201)
        body = valid_response.json()
        self.assertEqual(
            body["createdBy"], {"type": "human", "id": "user-1", "displayName": "Ada"}
        )
        self.assertEqual(body["status"], "READY")
        self.assertEqual(body["contractVersion"], 1)
        self.assertIsNotNone(repository.mission)
        self.assertIsNotNone(repository.contract)
        self.assertEqual(
            repository.contract_lineage_workspaces,
            {"contract-1": "user-1"},
        )
        self.assertEqual(
            repository.contract.governance.decision_timeout_seconds,
            86_400,
        )
        self.assertEqual(len(repository.events), 1)
        event = repository.events[0]
        self.assertEqual(event.sequence, 1)
        self.assertEqual(event.event_type, "mission.lifecycle.created")
        self.assertEqual(event.actor.id, "user-1")
        self.assertEqual(event.payload["contractVersion"], 1)

    def test_create_mission_cannot_create_uncontrolled_contract_revision(
        self,
    ) -> None:
        repository = FakeMissionRepository()
        user = {"id": "user-1", "name": "Ada", "role": "developer"}
        client = TestClient(build_app(repository, user))

        response = client.post(
            "/api/v1/missions",
            json={
                "workspaceId": "user-1",
                "title": "Bypass revision governance",
                "objective": "Create version two without a revision command.",
                "source": {"type": "api"},
                "contract": build_contract(version=2).to_public_dict(),
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("must start at version 1", response.json()["detail"])
        self.assertIsNone(repository.contract)
        self.assertIsNone(repository.mission)

    def test_contract_revision_is_fenced_and_does_not_rebind_mission(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="workspace-1")
        repository.contract = build_contract(version=1)
        repository.contract_lineage_workspaces["contract-1"] = "workspace-1"
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
            )
        )
        revised_contract = build_contract(
            version=2,
            governance={"decisionTimeoutSeconds": 1800},
        )

        response = client.post(
            "/api/v1/missions/mis-1/contract/revisions",
            json={
                "expectedVersion": 1,
                "reason": "Extend the human review window.",
                "contract": revised_contract.to_public_dict(),
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["version"], 2)
        self.assertEqual(repository.contract, revised_contract)
        self.assertEqual(repository.mission.contract_version, 1)
        self.assertEqual(repository.contract_lineage_locks, ["contract-1"])
        self.assertEqual(len(repository.events), 1)
        event = repository.events[0]
        self.assertEqual(event.aggregate_type.value, "mission_contract")
        self.assertEqual(event.aggregate_id, "contract-1")
        self.assertEqual(event.event_type, "contract.lifecycle.revised")
        self.assertEqual(
            event.payload,
            {
                "sourceMissionId": "mis-1",
                "previousVersion": 1,
                "version": 2,
                "reason": "Extend the human review window.",
            },
        )

        stale = client.post(
            "/api/v1/missions/mis-1/contract/revisions",
            json={
                "expectedVersion": 1,
                "reason": "Attempt a concurrent overwrite.",
                "contract": build_contract(version=2).to_public_dict(),
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertIn("expected=1, current=2", stale.json()["detail"])
        self.assertEqual(len(repository.contracts), 1)
        self.assertEqual(len(repository.events), 1)
        self.assertEqual(repository.mission.contract_version, 1)

    def test_contract_revision_rejects_lineage_and_version_drift(self) -> None:
        for name, contract, message in (
            (
                "lineage",
                build_contract(id="other-contract", version=2),
                "lineage does not match",
            ),
            (
                "version",
                build_contract(version=3),
                "increment version by one",
            ),
        ):
            with self.subTest(name=name):
                repository = FakeMissionRepository()
                repository.mission = build_mission(workspace_id="workspace-1")
                repository.contract = build_contract(version=1)
                repository.contract_lineage_workspaces["contract-1"] = "workspace-1"
                client = TestClient(
                    build_app(
                        repository,
                        {
                            "id": "workspace-1",
                            "name": "Ada",
                            "role": "developer",
                        },
                    )
                )

                response = client.post(
                    "/api/v1/missions/mis-1/contract/revisions",
                    json={
                        "expectedVersion": 1,
                        "reason": "Invalid revision.",
                        "contract": contract.to_public_dict(),
                    },
                )

                self.assertEqual(response.status_code, 409)
                self.assertIn(message, response.json()["detail"])
                self.assertEqual(repository.contracts, [])
                self.assertEqual(repository.events, [])
                self.assertEqual(repository.mission.contract_version, 1)

    def test_contract_revision_authorization_precedes_lineage_lock(self) -> None:
        for name, user in (
            (
                "cross_workspace",
                {"id": "other-user", "name": "Mallory", "role": "developer"},
            ),
            (
                "service",
                {"id": "workspace-1", "name": "Runner", "role": "runner"},
            ),
        ):
            with self.subTest(name=name):
                repository = FakeMissionRepository()
                repository.mission = build_mission(workspace_id="workspace-1")
                repository.contract = build_contract(version=1)
                repository.contract_lineage_workspaces["contract-1"] = "workspace-1"
                client = TestClient(build_app(repository, user))

                response = client.post(
                    "/api/v1/missions/mis-1/contract/revisions",
                    json={
                        "expectedVersion": 1,
                        "reason": "Unauthorized revision.",
                        "contract": build_contract(version=2).to_public_dict(),
                    },
                )

                self.assertEqual(response.status_code, 403)
                self.assertEqual(repository.contract_lineage_locks, [])
                self.assertEqual(repository.contracts, [])
                self.assertEqual(repository.events, [])

    def test_contract_revision_fails_when_materialized_owner_is_missing(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="workspace-1")
        repository.contract = build_contract(version=1)
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/contract/revisions",
            json={
                "expectedVersion": 1,
                "reason": "Owner must be durable.",
                "contract": build_contract(version=2).to_public_dict(),
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("workspace ownership is missing", response.json()["detail"])
        self.assertEqual(repository.contracts, [])
        self.assertEqual(repository.events, [])

    def test_contract_lineage_cannot_cross_workspace_boundaries(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="workspace-1")
        repository.contract = build_contract(version=1)
        repository.contract_lineage_workspaces["contract-1"] = "workspace-1"
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-2", "name": "Bob", "role": "developer"},
            )
        )

        create_response = client.post(
            "/api/v1/missions",
            json={
                "workspaceId": "workspace-2",
                "title": "Cross-workspace reuse",
                "objective": "Attempt to reuse another workspace lineage.",
                "source": {"type": "api"},
                "contract": build_contract(version=1).to_public_dict(),
            },
        )

        self.assertEqual(create_response.status_code, 409)
        self.assertIn("belongs to another workspace", create_response.json()["detail"])
        self.assertEqual(repository.mission.workspace_id, "workspace-1")
        self.assertEqual(repository.contracts, [])
        self.assertEqual(repository.events, [])

        repository.contract_lineage_workspaces["contract-1"] = "workspace-2"
        workspace_one_client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
            )
        )
        revise_response = workspace_one_client.post(
            "/api/v1/missions/mis-1/contract/revisions",
            json={
                "expectedVersion": 1,
                "reason": "Attempt to revise a shared lineage.",
                "contract": build_contract(version=2).to_public_dict(),
            },
        )

        self.assertEqual(revise_response.status_code, 409)
        self.assertIn("belongs to another workspace", revise_response.json()["detail"])
        self.assertEqual(repository.contracts, [])
        self.assertEqual(repository.events, [])

    def test_non_admin_cannot_access_another_workspace(self) -> None:
        repository = FakeMissionRepository()
        user = {"id": "user-1", "name": "Ada", "role": "developer"}
        client = TestClient(build_app(repository, user))

        response = client.get("/api/v1/missions?workspaceId=workspace-2")

        self.assertEqual(response.status_code, 403)

    def test_get_and_list_return_public_contracts(self) -> None:
        repository = FakeMissionRepository()
        mission = build_mission(workspace_id="workspace-1")
        repository.mission = mission
        repository.list_result = [mission]
        user = {"id": "admin-1", "name": "Root", "role": "admin"}
        client = TestClient(build_app(repository, user))

        get_response = client.get(f"/api/v1/missions/{mission.id}")
        list_response = client.get("/api/v1/missions?workspaceId=workspace-1")

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()["workspaceId"], "workspace-1")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["missions"][0]["id"], mission.id)

    def test_workspace_decision_inbox_applies_controlled_filters(self) -> None:
        repository = FakeMissionRepository()
        mission = build_mission(id="mis-1", workspace_id="workspace-1")
        repository.mission = mission
        repository.list_result = [
            mission,
            build_mission(id="mis-2", workspace_id="workspace-1"),
            build_mission(id="mis-other", workspace_id="workspace-2"),
        ]
        pending = build_decision(id="dec-1", mission_id="mis-1")
        repository.decisions = [
            pending,
            build_decision(id="dec-2", mission_id="mis-2"),
            build_decision(id="dec-other", mission_id="mis-other"),
            build_decision(
                id="dec-resolved",
                mission_id="mis-1",
                status="RESOLVED",
                version=2,
                resolution="FAIL_MISSION",
                rationale="The Mission cannot currently be verified.",
                resolved_by={"type": "human", "id": "user-1"},
                resolved_at=pending.requested_at,
            ),
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.get(
            "/api/v1/missions/decisions",
            params={
                "workspaceId": "workspace-1",
                "status": "PENDING",
                "missionId": "mis-1",
                "reasonCode": "no_applicable_policy",
                "limit": 20,
                "offset": 0,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [decision["id"] for decision in response.json()["decisions"]],
            ["dec-1"],
        )
        self.assertEqual(
            repository.workspace_decision_calls,
            [
                (
                    "workspace-1",
                    DecisionStatus.PENDING,
                    "mis-1",
                    EvaluationPolicyReason.NO_APPLICABLE_POLICY,
                    20,
                    0,
                )
            ],
        )

        all_response = client.get(
            "/api/v1/missions/decisions",
            params={"workspaceId": "workspace-1", "status": "ALL"},
        )
        self.assertEqual(all_response.status_code, 200)
        self.assertEqual(
            {decision["id"] for decision in all_response.json()["decisions"]},
            {"dec-1", "dec-2", "dec-resolved"},
        )
        self.assertIsNone(repository.workspace_decision_calls[-1][1])

        default_response = client.get(
            "/api/v1/missions/decisions",
            params={"workspaceId": "workspace-1"},
        )
        self.assertEqual(default_response.status_code, 200)
        self.assertEqual(
            {decision["id"] for decision in default_response.json()["decisions"]},
            {"dec-1", "dec-2"},
        )
        self.assertEqual(
            repository.workspace_decision_calls[-1][1], DecisionStatus.PENDING
        )

    def test_workspace_decision_inbox_denies_before_repository_query(self) -> None:
        for user, workspace_id in (
            (
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
                "workspace-2",
            ),
            (
                {"id": "workspace-1", "name": "Verifier", "role": "verifier"},
                "workspace-1",
            ),
        ):
            with self.subTest(user=user, workspace_id=workspace_id):
                repository = FakeMissionRepository()
                client = TestClient(build_app(repository, user))

                response = client.get(
                    "/api/v1/missions/decisions",
                    params={"workspaceId": workspace_id},
                )

                self.assertEqual(response.status_code, 403)
                self.assertEqual(repository.workspace_decision_calls, [])

    def test_start_updates_snapshot_and_appends_actor_event(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1")
        repository.events = [build_event()]
        user = {"id": "user-1", "name": "Ada", "role": "developer"}
        client = TestClient(build_app(repository, user))

        response = client.post("/api/v1/missions/mis-1/start")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "RUNNING")
        self.assertIsNotNone(repository.mission)
        self.assertEqual(repository.mission.status.value, "RUNNING")
        self.assertEqual(len(repository.events), 2)
        event = repository.events[-1]
        self.assertEqual(event.sequence, 2)
        self.assertEqual(event.event_type, "mission.lifecycle.started")
        self.assertEqual(event.actor.id, "user-1")
        self.assertEqual(
            event.payload,
            {"previousStatus": "READY", "status": "RUNNING"},
        )

    def test_cancel_ready_mission(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1")
        repository.events = [build_event()]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "CANCELLED")
        self.assertEqual(
            repository.events[-1].event_type, "mission.lifecycle.cancelled"
        )

    def test_cancel_mission_cancels_non_terminal_work_units(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.events = [build_event()]
        repository.work_units = [
            build_work_unit(id="wu-pending"),
            build_work_unit(
                id="wu-running",
                status="RUNNING",
                lease=Lease(
                    id="lease-running",
                    runner_id="runner-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            ),
            build_work_unit(id="wu-succeeded", status="SUCCEEDED"),
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "CANCELLED")
        by_id = {work_unit.id: work_unit for work_unit in repository.work_units}
        self.assertEqual(by_id["wu-pending"].status.value, "CANCELLED")
        self.assertEqual(by_id["wu-running"].status.value, "CANCELLED")
        self.assertIsNone(by_id["wu-running"].lease)
        self.assertEqual(by_id["wu-succeeded"].status.value, "SUCCEEDED")
        cancellation_events = repository.events[1:]
        self.assertEqual(
            [event.event_type for event in cancellation_events],
            [
                "mission.lifecycle.cancelled",
                "work_unit.lifecycle.cancelled",
                "work_unit.lifecycle.cancelled",
            ],
        )
        self.assertTrue(
            all(
                event.causation_id == cancellation_events[0].event_id
                for event in cancellation_events[1:]
            )
        )

    def test_cancel_waiting_mission_closes_pending_decision(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="WAITING_DECISION",
        )
        repository.work_units = [build_work_unit(status="VERIFYING", attempt=1)]
        decision = build_decision()
        repository.decisions = [decision]
        repository.events = [
            build_event(
                event_id="evt-decision-requested",
                aggregate_type="decision",
                aggregate_id=decision.id,
                event_type="decision.lifecycle.requested",
                payload=decision.to_public_dict(),
            ),
            build_event(
                event_id="evt-mission-waiting",
                event_type="mission.lifecycle.waiting_decision",
                payload={
                    "previousStatus": "RUNNING",
                    "status": "WAITING_DECISION",
                    "decisionId": decision.id,
                    "workUnitId": "wu-1",
                },
            ),
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repository.mission.status.value, "CANCELLED")
        self.assertEqual(repository.work_units[0].status.value, "CANCELLED")
        self.assertEqual(repository.decisions[0].status.value, "CANCELLED")
        self.assertEqual(repository.decisions[0].version, 2)
        self.assertIsNone(repository.decisions[0].resolution)
        self.assertEqual(
            [event.event_type for event in repository.events[-3:]],
            [
                "mission.lifecycle.cancelled",
                "decision.lifecycle.cancelled",
                "work_unit.lifecycle.cancelled",
            ],
        )
        self.assertEqual(
            repository.events[-2].causation_id,
            repository.events[-3].event_id,
        )

    def test_invalid_transition_returns_conflict_without_event(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
        )
        repository.events = [build_event()]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/start")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(repository.events), 1)
        self.assertEqual(repository.mission.status.value, "RUNNING")

    def test_lifecycle_command_returns_not_found(self) -> None:
        client = TestClient(
            build_app(
                FakeMissionRepository(),
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/missing/start")

        self.assertEqual(response.status_code, 404)

    def test_events_are_ordered_filtered_and_workspace_scoped(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="workspace-1")
        repository.events = [
            build_event(
                sequence=2, event_id="evt-2", event_type="mission.lifecycle.started"
            ),
            build_event(),
        ]
        admin_client = TestClient(
            build_app(
                repository,
                {"id": "admin-1", "name": "Root", "role": "admin"},
            )
        )

        response = admin_client.get("/api/v1/missions/mis-1/events?afterSequence=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [event["sequence"] for event in response.json()["events"]],
            [2],
        )

        other_user_client = TestClient(
            build_app(
                repository,
                {"id": "user-2", "name": "Grace", "role": "developer"},
            )
        )
        denied = other_user_client.get("/api/v1/missions/mis-1/events")
        self.assertEqual(denied.status_code, 403)

    def test_create_and_list_pending_work_unit_with_event(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units",
            json={
                "id": "wu-api-1",
                "kind": "code_change",
                "expectedOutputs": [{"kind": "diff", "required": True}],
                "requiredCapabilities": ["repository.write"],
                "assignedAdapter": "codex",
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "PENDING")
        self.assertEqual(response.json()["attempt"], 0)
        self.assertEqual(len(repository.work_units), 1)
        event = repository.events[-1]
        self.assertEqual(event.aggregate_type.value, "work_unit")
        self.assertEqual(event.aggregate_id, "wu-api-1")
        self.assertEqual(event.event_type, "work_unit.lifecycle.created")
        self.assertEqual(event.actor.id, "user-1")
        self.assertEqual(event.correlation_id, "mis-1")

        listed = client.get("/api/v1/missions/mis-1/work-units")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["workUnits"][0]["id"], "wu-api-1")

    def test_work_unit_requires_running_mission_and_contract_capability(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1")
        repository.contract = build_contract()
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )
        request = {
            "kind": "code_change",
            "requiredCapabilities": ["production.deploy"],
        }

        not_running = client.post(
            "/api/v1/missions/mis-1/work-units",
            json=request,
        )
        self.assertEqual(not_running.status_code, 409)

        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        unsupported = client.post(
            "/api/v1/missions/mis-1/work-units",
            json=request,
        )
        self.assertEqual(unsupported.status_code, 409)
        self.assertEqual(repository.work_units, [])
        self.assertEqual(repository.events, [])

    def test_delegate_work_unit_requires_active_lease_and_registered_artifact(
        self,
    ) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                id="wu-parent",
                status="RUNNING",
                attempt=2,
                lease=Lease(
                    id="lease-parent",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        repository.artifacts = [build_artifact(work_unit_id="wu-parent", attempt=2)]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
                agent_binding_resolver=StaticAgentBindingResolver(
                    {
                        ("user-1", "reviewer"): AgentBinding(
                            agent_id="reviewer",
                            adapter_type="local_codex",
                            capabilities=("repository.write",),
                        )
                    }
                ),
            )
        )
        request = {
            "id": "wu-child",
            "agentId": "reviewer",
            "leaseId": "lease-parent",
            "inputRefs": [
                {"id": "artifact-1", "digest": repository.artifacts[0].digest}
            ],
            "requiredCapabilities": ["repository.write"],
        }

        first = client.post(
            "/api/v1/missions/mis-1/work-units/wu-parent/delegations",
            json=request,
        )
        second = client.post(
            "/api/v1/missions/mis-1/work-units/wu-parent/delegations",
            json=request,
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json()["parentWorkUnitId"], "wu-parent")
        self.assertEqual(len(repository.work_units), 2)
        self.assertEqual(len(repository.events), 2)
        self.assertEqual(
            repository.events[0].event_type, "work_unit.delegation.requested"
        )
        self.assertEqual(repository.events[1].event_type, "work_unit.lifecycle.created")
        self.assertEqual(
            repository.events[1].causation_id,
            repository.events[0].event_id,
        )

        repository.work_units[0] = build_work_unit(
            id="wu-parent",
            status="RUNNING",
            attempt=2,
            lease=Lease(
                id="lease-parent",
                runner_id="user-1",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ),
        )
        rejected = client.post(
            "/api/v1/missions/mis-1/work-units/wu-parent/delegations",
            json={**request, "id": "wu-child-2", "inputRefs": []},
        )
        self.assertEqual(rejected.status_code, 422)

        restricted_client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
                agent_binding_resolver=StaticAgentBindingResolver(
                    {
                        ("user-1", "reviewer"): AgentBinding(
                            agent_id="reviewer",
                            adapter_type="local_codex",
                            capabilities=("repository.read",),
                        )
                    }
                ),
            )
        )
        capability_gap = restricted_client.post(
            "/api/v1/missions/mis-1/work-units/wu-parent/delegations",
            json={**request, "id": "wu-child-3"},
        )
        self.assertEqual(capability_gap.status_code, 409)
        self.assertEqual(len(repository.work_units), 2)

    def test_delegate_work_unit_fails_closed_without_authorized_binding(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                id="wu-parent",
                status="RUNNING",
                lease=Lease(
                    id="lease-parent",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        repository.artifacts = [build_artifact(work_unit_id="wu-parent")]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-parent/delegations",
            json={
                "id": "wu-child",
                "agentId": "reviewer",
                "leaseId": "lease-parent",
                "inputRefs": [
                    {"id": "artifact-1", "digest": repository.artifacts[0].digest}
                ],
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(repository.work_units), 1)
        self.assertEqual(repository.events, [])

    def test_work_unit_rejects_dependency_outside_mission(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(id="wu-other", mission_id="mis-other")]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units",
            json={"kind": "code_change", "dependencies": ["wu-other"]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(repository.work_units), 1)
        self.assertEqual(repository.events, [])

    def test_lease_claim_updates_attempt_and_appends_event(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [build_work_unit()]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/lease",
            json={"leaseSeconds": 60},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "LEASED")
        self.assertEqual(body["attempt"], 1)
        self.assertEqual(body["lease"]["runnerId"], "user-1")
        self.assertEqual(len(repository.events), 1)
        event = repository.events[-1]
        self.assertEqual(event.sequence, 1)
        self.assertEqual(event.event_type, "work_unit.lifecycle.leased")
        self.assertEqual(event.actor.id, "user-1")
        self.assertEqual(event.payload["attempt"], 1)

    def test_lease_claim_requires_completed_dependencies(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(id="wu-dependency"),
            build_work_unit(id="wu-child", dependencies=["wu-dependency"]),
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/work-units/wu-child/lease")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[1].status.value, "PENDING")
        self.assertEqual(repository.events, [])

    def test_delegated_claim_selects_matching_ready_unit_atomically(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(id="wu-parent", status="SUCCEEDED"),
            build_work_unit(
                id="wu-child",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            ),
            build_work_unit(
                id="wu-other-agent",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="other",
                assigned_adapter="local_codex",
            ),
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={
                "agentId": "reviewer",
                "adapterType": "local_codex",
                "leaseSeconds": 60,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["claimStatus"], "claimed")
        body = response.json()["workUnit"]
        self.assertEqual(body["id"], "wu-child")
        self.assertEqual(body["status"], "LEASED")
        self.assertEqual(body["attempt"], 1)
        self.assertEqual(body["lease"]["runnerId"], "user-1")
        self.assertEqual(repository.events[-1].actor.type.value, "runner")
        self.assertEqual(repository.events[-1].payload["claimMode"], "delegated")

    def test_mission_scoped_claim_cannot_bypass_tenant_concurrency(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
        )
        repository.work_units = [
            build_work_unit(
                id="wu-child",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            )
        ]
        repository.tenant_active_count_override = 1
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
                workspace_claim_admission_policy_resolver=(
                    FakeWorkspaceClaimAdmissionPolicyResolver(
                        WorkspaceClaimAdmissionPolicy(
                            tenant_id="tenant-1",
                            max_concurrent=1,
                        )
                    )
                ),
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={
                "agentId": "reviewer",
                "adapterType": "local_codex",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["claimStatus"],
            "capacity_saturated",
        )
        self.assertIsNone(response.json()["workUnit"])
        self.assertEqual(repository.admission_locks, ["tenant-1"])
        self.assertEqual(repository.work_units[0].status.value, "PENDING")
        self.assertEqual(repository.events, [])

    def test_workspace_claim_selects_least_loaded_authorized_mission(self) -> None:
        repository = FakeMissionRepository()
        first = build_mission(
            id="mis-first",
            workspace_id="workspace-1",
            status="RUNNING",
        )
        second = build_mission(
            id="mis-second",
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.mission = first
        repository.list_result = [first, second]
        repository.work_units = [
            build_work_unit(
                id="wu-active",
                mission_id="mis-first",
                status="VERIFYING",
            ),
            build_work_unit(
                id="wu-first",
                mission_id="mis-first",
                parent_work_unit_id="wu-parent-first",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            ),
            build_work_unit(
                id="wu-second",
                mission_id="mis-second",
                parent_work_unit_id="wu-parent-second",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            ),
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/work-unit-claims",
            json={
                "workspaceId": "workspace-1",
                "agentId": "reviewer",
                "adapterType": "local_codex",
                "leaseSeconds": 60,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["claimStatus"], "claimed")
        claimed = response.json()["workUnit"]
        self.assertEqual(claimed["id"], "wu-second")
        self.assertEqual(claimed["missionId"], "mis-second")
        self.assertEqual(claimed["lease"]["runnerId"], "workspace-1")
        self.assertEqual(repository.events[-1].correlation_id, "mis-second")
        self.assertEqual(repository.admission_locks, [])

    def test_workspace_claim_stops_at_tenant_concurrency_limit(self) -> None:
        repository = FakeMissionRepository()
        mission = build_mission(
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.mission = mission
        repository.list_result = [mission]
        repository.work_units = [
            build_work_unit(
                id="wu-ready",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            )
        ]
        repository.tenant_active_count_override = 1
        admission_resolver = FakeWorkspaceClaimAdmissionPolicyResolver(
            WorkspaceClaimAdmissionPolicy(
                tenant_id="tenant-1",
                max_concurrent=1,
            )
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Runner", "role": "runner"},
                workspace_claim_admission_policy_resolver=admission_resolver,
            )
        )

        response = client.post(
            "/api/v1/missions/work-unit-claims",
            json={
                "workspaceId": "workspace-1",
                "agentId": "reviewer",
                "adapterType": "local_codex",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["claimStatus"],
            "capacity_saturated",
        )
        self.assertIsNone(response.json()["workUnit"])
        self.assertEqual(admission_resolver.calls, ["workspace-1"])
        self.assertEqual(repository.admission_locks, ["tenant-1"])
        self.assertEqual(repository.work_units[0].status.value, "PENDING")
        self.assertEqual(repository.events, [])

    def test_workspace_claim_fails_closed_when_admission_policy_is_unavailable(
        self,
    ) -> None:
        class RepositoryThatMustNotBeCalled(FakeMissionRepository):
            @asynccontextmanager
            async def transaction(self):
                raise AssertionError("policy resolution must precede repository access")
                yield self

        repository = RepositoryThatMustNotBeCalled()
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Runner", "role": "runner"},
                workspace_claim_admission_policy_resolver=(
                    FakeWorkspaceClaimAdmissionPolicyResolver(
                        error=WorkspaceClaimAdmissionUnavailableError(
                            "workspace admission unavailable"
                        )
                    )
                ),
            )
        )

        response = client.post(
            "/api/v1/missions/work-unit-claims",
            json={
                "workspaceId": "workspace-1",
                "agentId": "reviewer",
                "adapterType": "local_codex",
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "workspace admission unavailable")
        self.assertEqual(repository.events, [])

    def test_workspace_claim_rejects_inactive_tenant(self) -> None:
        repository = FakeMissionRepository()
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Runner", "role": "runner"},
                workspace_claim_admission_policy_resolver=(
                    FakeWorkspaceClaimAdmissionPolicyResolver(
                        error=WorkspaceClaimAdmissionDeniedError(
                            "Workspace tenant is not active"
                        )
                    )
                ),
            )
        )

        response = client.post(
            "/api/v1/missions/work-unit-claims",
            json={
                "workspaceId": "workspace-1",
                "agentId": "reviewer",
                "adapterType": "local_codex",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Workspace tenant is not active")
        self.assertEqual(repository.events, [])

    def test_workspace_claim_fails_closed_when_admission_state_is_unavailable(
        self,
    ) -> None:
        class FailingAdmissionRepository(FakeMissionRepository):
            async def lock_tenant_claim_admission(self, tenant_id: str) -> None:
                del tenant_id
                raise ConnectionError("database unavailable")

        repository = FailingAdmissionRepository()
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Runner", "role": "runner"},
                workspace_claim_admission_policy_resolver=(
                    FakeWorkspaceClaimAdmissionPolicyResolver(
                        WorkspaceClaimAdmissionPolicy(
                            tenant_id="tenant-1",
                            max_concurrent=1,
                        )
                    )
                ),
            )
        )

        response = client.post(
            "/api/v1/missions/work-unit-claims",
            json={
                "workspaceId": "workspace-1",
                "agentId": "reviewer",
                "adapterType": "local_codex",
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Workspace claim admission state is unavailable",
        )
        self.assertEqual(repository.events, [])

    def test_workspace_claim_rejects_unauthorized_workspace(self) -> None:
        repository = FakeMissionRepository()
        grant_authorizer = FakeRunnerWorkspaceGrantAuthorizer()
        client = TestClient(
            build_app(
                repository,
                {"id": "runner-1", "name": "Runner", "role": "runner"},
                runner_workspace_grant_authorizer=grant_authorizer,
            )
        )

        response = client.post(
            "/api/v1/missions/work-unit-claims",
            json={
                "workspaceId": "workspace-other",
                "agentId": "reviewer",
                "adapterType": "local_codex",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            grant_authorizer.calls,
            [("workspace-other", "runner-1")],
        )
        self.assertEqual(repository.events, [])

    def test_workspace_claim_accepts_explicit_runner_service_grant(self) -> None:
        repository = FakeMissionRepository()
        mission = build_mission(
            id="mis-granted",
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.mission = mission
        repository.list_result = [mission]
        repository.work_units = [
            build_work_unit(
                id="wu-granted",
                mission_id=mission.id,
                parent_work_unit_id="wu-parent",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            )
        ]
        grant_authorizer = FakeRunnerWorkspaceGrantAuthorizer(
            {("workspace-1", "runner-a")}
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "runner-a", "name": "Runner A", "role": "runner"},
                runner_workspace_grant_authorizer=grant_authorizer,
            )
        )

        response = client.post(
            "/api/v1/missions/work-unit-claims",
            json={
                "workspaceId": "workspace-1",
                "agentId": "reviewer",
                "adapterType": "local_codex",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workUnit"]["id"], "wu-granted")
        self.assertEqual(
            response.json()["workUnit"]["lease"]["runnerId"],
            "runner-a",
        )
        self.assertEqual(grant_authorizer.calls, [("workspace-1", "runner-a")])

    def test_workspace_claim_fails_closed_when_grants_are_unavailable(self) -> None:
        repository = FakeMissionRepository()
        grant_authorizer = FakeRunnerWorkspaceGrantAuthorizer(
            error=RunnerWorkspaceGrantUnavailableError("workspace ACL unavailable")
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "runner-a", "name": "Runner A", "role": "runner"},
                runner_workspace_grant_authorizer=grant_authorizer,
            )
        )

        response = client.post(
            "/api/v1/missions/work-unit-claims",
            json={
                "workspaceId": "workspace-1",
                "agentId": "reviewer",
                "adapterType": "local_codex",
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "workspace ACL unavailable")
        self.assertEqual(repository.events, [])

    def test_workspace_claim_rejects_non_runner_even_with_grant(self) -> None:
        repository = FakeMissionRepository()
        grant_authorizer = FakeRunnerWorkspaceGrantAuthorizer(
            {("workspace-1", "user-1")}
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
                runner_workspace_grant_authorizer=grant_authorizer,
            )
        )

        response = client.post(
            "/api/v1/missions/work-unit-claims",
            json={
                "workspaceId": "workspace-1",
                "agentId": "reviewer",
                "adapterType": "local_codex",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Runner access required")
        self.assertEqual(grant_authorizer.calls, [])
        self.assertEqual(repository.events, [])

    def test_workspace_claim_denial_precedes_repository_transaction(self) -> None:
        class RepositoryThatMustNotBeCalled(FakeMissionRepository):
            @asynccontextmanager
            async def transaction(self):
                raise AssertionError("authorization must precede repository access")
                yield self

        repository = RepositoryThatMustNotBeCalled()
        client = TestClient(
            build_app(
                repository,
                {"id": "runner-a", "name": "Runner A", "role": "runner"},
                runner_workspace_grant_authorizer=(
                    FakeRunnerWorkspaceGrantAuthorizer()
                ),
            )
        )

        response = client.post(
            "/api/v1/missions/work-unit-claims",
            json={
                "workspaceId": "workspace-1",
                "agentId": "reviewer",
                "adapterType": "local_codex",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(repository.events, [])

    def test_runner_execution_requires_its_claimed_lease(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.work_units = [
            build_work_unit(
                status="LEASED",
                lease=Lease(
                    id="lease-a",
                    runner_id="runner-a",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "runner-b", "name": "Runner B", "role": "runner"},
                runner_workspace_grant_authorizer=(
                    FakeRunnerWorkspaceGrantAuthorizer({("workspace-1", "runner-b")})
                ),
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/start",
            json={"leaseId": "lease-a"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "Active Runner lease ownership required",
        )
        self.assertEqual(repository.work_units[0].status.value, "LEASED")
        self.assertEqual(repository.events, [])

    def test_workspace_claim_rejects_repository_scope_escape(self) -> None:
        class EscapingRepository(FakeMissionRepository):
            async def get_workspace_bound_work_unit_for_claim(
                self,
                workspace_id: str,
                *,
                agent_id: str,
                adapter_type: str,
            ) -> tuple[Mission, WorkUnit] | None:
                del workspace_id, agent_id, adapter_type
                return self.list_result[0], self.work_units[0]

        repository = EscapingRepository()
        repository.list_result = [
            build_mission(
                id="mis-other",
                workspace_id="workspace-other",
                status="RUNNING",
            )
        ]
        repository.work_units = [
            build_work_unit(
                id="wu-other",
                mission_id="mis-other",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/work-unit-claims",
            json={
                "workspaceId": "workspace-1",
                "agentId": "reviewer",
                "adapterType": "local_codex",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].status.value, "PENDING")
        self.assertEqual(repository.events, [])

    def test_bound_claim_selects_a2a_inbound_root_atomically(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
            source=MissionSource(
                type="a2a.inbound",
                reference="https://sender.example.test",
                external_id="remote-task-1",
            ),
        )
        repository.work_units = [
            build_work_unit(
                id="wu-inbound",
                kind="a2a.inbound",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
                required_capabilities=["a2a.receive", "repository.read"],
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={
                "agentId": "reviewer",
                "adapterType": "local_codex",
                "leaseSeconds": 60,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["claimStatus"], "claimed")
        body = response.json()["workUnit"]
        self.assertEqual(body["id"], "wu-inbound")
        self.assertEqual(body["status"], "LEASED")
        self.assertEqual(body["attempt"], 1)
        self.assertEqual(body["lease"]["runnerId"], "user-1")
        self.assertEqual(repository.events[-1].payload["claimMode"], "a2a.inbound")

    def test_bound_claim_selects_catalog_bound_a2a_outbound_root(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
            source=MissionSource(
                type="a2a",
                reference="https://receiver.example.test/a2a",
                external_id="remote-task-1",
            ),
        )
        repository.work_units = [
            build_work_unit(
                id="wu-outbound",
                kind="a2a.delegate",
                assigned_agent_id="outbound-dispatcher",
                assigned_adapter="a2a.outbound",
                required_capabilities=["a2a.send", "artifact.write"],
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={
                "agentId": "outbound-dispatcher",
                "adapterType": "a2a.outbound",
                "leaseSeconds": 60,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["claimStatus"], "claimed")
        body = response.json()["workUnit"]
        self.assertEqual(body["id"], "wu-outbound")
        self.assertEqual(body["status"], "LEASED")
        self.assertEqual(body["attempt"], 1)
        self.assertEqual(body["lease"]["runnerId"], "user-1")
        self.assertEqual(repository.events[-1].payload["claimMode"], "a2a.outbound")

    def test_bound_claim_selects_started_mission_fork_root(self) -> None:
        for path in (
            "/api/v1/missions/mis-1/work-unit-claims",
            "/api/v1/missions/work-unit-claims",
        ):
            with self.subTest(path=path):
                repository = FakeMissionRepository()
                repository.mission = build_mission(
                    workspace_id="user-1",
                    status="RUNNING",
                    source=MissionSource(
                        type="mission.fork",
                        reference="mis-source",
                        external_id="chk-source",
                    ),
                )
                repository.work_units = [
                    build_work_unit(
                        id="wu-fork",
                        kind="mission.fork",
                        assigned_agent_id="reviewer",
                        assigned_adapter="local_codex",
                        input_refs=[{"id": "artifact-source", "digest": DIGEST}],
                    )
                ]
                client = TestClient(
                    build_app(
                        repository,
                        {"id": "user-1", "name": "Runner", "role": "runner"},
                    )
                )
                request: dict[str, object] = {
                    "agentId": "reviewer",
                    "adapterType": "local_codex",
                }
                if path.endswith("/missions/work-unit-claims"):
                    request["workspaceId"] = "user-1"

                response = client.post(path, json=request)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["claimStatus"], "claimed")
                self.assertEqual(response.json()["workUnit"]["id"], "wu-fork")
                self.assertEqual(response.json()["workUnit"]["status"], "LEASED")
                self.assertEqual(response.json()["workUnit"]["attempt"], 1)
                self.assertEqual(
                    repository.events[-1].payload["claimMode"],
                    "mission.fork",
                )

    def test_bound_claim_rejects_mismatched_or_outbound_fork_root(self) -> None:
        cases = (
            ("issue", "mission.fork", "local_codex"),
            ("mission.fork", "code_change", "local_codex"),
            ("mission.fork", "mission.fork", "a2a.outbound"),
        )
        for source_type, kind, adapter in cases:
            with self.subTest(source_type=source_type, kind=kind, adapter=adapter):
                repository = FakeMissionRepository()
                source = (
                    MissionSource(
                        type="mission.fork",
                        reference="mis-source",
                        external_id="chk-source",
                    )
                    if source_type == "mission.fork"
                    else MissionSource(type="issue", reference="issue-1")
                )
                repository.mission = build_mission(
                    workspace_id="user-1",
                    status="RUNNING",
                    source=source,
                )
                repository.work_units = [
                    build_work_unit(
                        id="wu-fork",
                        kind=kind,
                        assigned_agent_id="reviewer",
                        assigned_adapter=adapter,
                    )
                ]
                client = TestClient(
                    build_app(
                        repository,
                        {"id": "user-1", "name": "Runner", "role": "runner"},
                    )
                )

                response = client.post(
                    "/api/v1/missions/mis-1/work-unit-claims",
                    json={"agentId": "reviewer", "adapterType": adapter},
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["claimStatus"], "idle")
                self.assertIsNone(response.json()["workUnit"])
                self.assertEqual(repository.events, [])

    def test_bound_claim_does_not_admit_other_root_kind(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
            source=MissionSource(
                type="a2a.inbound",
                reference="https://sender.example.test",
                external_id="remote-task-1",
            ),
        )
        repository.work_units = [
            build_work_unit(
                id="wu-root",
                kind="code_change",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={"agentId": "reviewer", "adapterType": "local_codex"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["claimStatus"], "idle")
        self.assertIsNone(response.json()["workUnit"])
        self.assertEqual(repository.work_units[0].status.value, "PENDING")
        self.assertEqual(repository.events, [])

    def test_bound_claim_requires_a2a_inbound_mission_source_for_root(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                id="wu-inbound",
                kind="a2a.inbound",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={"agentId": "reviewer", "adapterType": "local_codex"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["claimStatus"], "idle")
        self.assertIsNone(response.json()["workUnit"])
        self.assertEqual(repository.work_units[0].status.value, "PENDING")
        self.assertEqual(repository.events, [])

    def test_bound_claim_rejects_recursive_outbound_inbound_root(self) -> None:
        class RecursiveCandidateRepository(FakeMissionRepository):
            async def get_bound_work_unit_for_claim(
                self,
                mission_id: str,
                *,
                agent_id: str,
                adapter_type: str,
                allowed_root_kind: str | None,
            ) -> WorkUnit | None:
                del mission_id, agent_id, adapter_type, allowed_root_kind
                return self.work_units[0]

        repository = RecursiveCandidateRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
            source=MissionSource(
                type="a2a.inbound",
                reference="https://sender.example.test",
                external_id="remote-task-1",
            ),
        )
        repository.work_units = [
            build_work_unit(
                id="wu-inbound",
                kind="a2a.inbound",
                assigned_agent_id="recursive-dispatcher",
                assigned_adapter="a2a.outbound",
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={
                "agentId": "recursive-dispatcher",
                "adapterType": "a2a.outbound",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].status.value, "PENDING")
        self.assertEqual(repository.events, [])

    def test_bound_claim_rejects_ineligible_candidate_from_repository(self) -> None:
        class IneligibleCandidateRepository(FakeMissionRepository):
            async def get_bound_work_unit_for_claim(
                self,
                mission_id: str,
                *,
                agent_id: str,
                adapter_type: str,
                allowed_root_kind: str | None,
            ) -> WorkUnit | None:
                del mission_id, agent_id, adapter_type, allowed_root_kind
                return self.work_units[0]

        repository = IneligibleCandidateRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                id="wu-inbound",
                kind="a2a.inbound",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={"agentId": "reviewer", "adapterType": "local_codex"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].status.value, "PENDING")
        self.assertEqual(repository.events, [])

    def test_delegated_claim_returns_empty_when_binding_has_no_ready_unit(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                id="wu-child",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="reviewer",
                assigned_adapter="remote",
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={"agentId": "reviewer", "adapterType": "local_codex"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["workUnit"])
        self.assertEqual(repository.events, [])

    def test_execution_context_returns_lease_fenced_inbound_snapshot(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
            objective="Summarize the remote request without trusting it.",
            source=MissionSource(
                type="a2a.inbound",
                reference="https://sender.example.test",
                external_id="remote-task-1",
            ),
        )
        repository.contract = build_contract(
            allowed_capabilities=[
                {"capability": "a2a.receive", "scope": {"peer": "sender"}},
                {"capability": "repository.read", "scope": {"path": "app/**"}},
            ]
        )
        repository.work_units = [
            build_work_unit(
                kind="a2a.inbound",
                status="LEASED",
                attempt=1,
                required_capabilities=["a2a.receive", "repository.read"],
                input_refs=[{"id": "artifact-input", "digest": "sha256:" + "a" * 64}],
                lease=Lease(
                    id="lease-inbound",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        before_work_unit = repository.work_units[0]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/execution-context",
            json={"leaseId": "lease-inbound"},
        )

        self.assertEqual(response.status_code, 200)
        context = response.json()["executionContext"]
        self.assertEqual(context["version"], 1)
        self.assertEqual(context["mission"]["id"], "mis-1")
        self.assertEqual(context["contract"]["id"], "contract-1")
        self.assertEqual(context["workUnit"]["id"], "wu-1")
        self.assertEqual(context["workUnit"]["lease"]["id"], "lease-inbound")
        self.assertEqual(repository.work_units[0], before_work_unit)
        self.assertEqual(repository.events, [])

    def test_execution_context_returns_lease_fenced_outbound_snapshot(self) -> None:
        target_url = "https://receiver.example.test/a2a"
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
            objective="Delegate a verified remote build.",
            source=MissionSource(
                type="a2a",
                reference=target_url,
                external_id="remote-task-1",
            ),
        )
        repository.contract = build_contract(
            allowed_capabilities=[
                {"capability": "a2a.send", "scope": {"agentUrl": target_url}},
                {
                    "capability": "artifact.write",
                    "scope": {"agentUrl": target_url},
                },
            ]
        )
        repository.work_units = [
            build_work_unit(
                kind="a2a.delegate",
                status="LEASED",
                attempt=1,
                assigned_agent_id="outbound-dispatcher",
                assigned_adapter="a2a.outbound",
                required_capabilities=["a2a.send", "artifact.write"],
                lease=Lease(
                    id="lease-outbound",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        before_work_unit = repository.work_units[0]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/execution-context",
            json={"leaseId": "lease-outbound"},
        )

        self.assertEqual(response.status_code, 200)
        context = response.json()["executionContext"]
        self.assertEqual(context["version"], 1)
        self.assertEqual(context["mission"]["source"]["type"], "a2a")
        self.assertEqual(context["mission"]["source"]["reference"], target_url)
        self.assertEqual(context["mission"]["source"]["externalId"], "remote-task-1")
        self.assertEqual(context["workUnit"]["kind"], "a2a.delegate")
        self.assertEqual(
            context["workUnit"]["assignedAgentId"],
            "outbound-dispatcher",
        )
        self.assertEqual(context["workUnit"]["assignedAdapter"], "a2a.outbound")
        self.assertEqual(context["workUnit"]["lease"]["id"], "lease-outbound")
        self.assertEqual(repository.work_units[0], before_work_unit)
        self.assertEqual(repository.events, [])

    def test_execution_context_returns_reference_only_mission_fork_snapshot(
        self,
    ) -> None:
        class NoAncestryIORepository(FakeMissionRepository):
            async def get_execution_checkpoint(
                self,
                checkpoint_id: str,
            ) -> ExecutionCheckpoint | None:
                del checkpoint_id
                raise AssertionError("fork context read source checkpoint content")

            async def get_artifact(self, artifact_id: str) -> Artifact | None:
                del artifact_id
                raise AssertionError("fork context read Artifact metadata or bytes")

        repository = NoAncestryIORepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
            objective="Continue from the independently verified diff.",
            source=MissionSource(
                type="mission.fork",
                reference="mis-source",
                external_id="chk-source",
            ),
        )
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                id="wu-fork",
                kind="mission.fork",
                status="LEASED",
                attempt=1,
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
                input_refs=[{"id": "artifact-source", "digest": DIGEST}],
                lease=Lease(
                    id="lease-fork",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        before_work_unit = repository.work_units[0]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-fork/execution-context",
            json={"leaseId": "lease-fork"},
        )

        self.assertEqual(response.status_code, 200)
        context = response.json()["executionContext"]
        self.assertEqual(context["version"], 1)
        self.assertEqual(context["mission"]["source"]["type"], "mission.fork")
        self.assertEqual(context["mission"]["source"]["reference"], "mis-source")
        self.assertEqual(
            context["mission"]["source"]["externalId"],
            "chk-source",
        )
        self.assertEqual(
            context["workUnit"]["inputRefs"],
            [{"id": "artifact-source", "digest": DIGEST}],
        )
        self.assertNotIn("contentAddress", context["workUnit"]["inputRefs"][0])
        self.assertEqual(context["workUnit"]["lease"]["id"], "lease-fork")
        self.assertEqual(repository.work_units[0], before_work_unit)
        self.assertEqual(repository.events, [])

    def test_execution_context_rejects_invalid_mission_fork_root_shape(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
            source=MissionSource(
                type="mission.fork",
                reference="mis-source",
                external_id="chk-source",
            ),
        )
        repository.contract = build_contract()
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )
        invalid_shapes = (
            {"kind": "code_change"},
            {"parent_work_unit_id": "wu-parent"},
            {"assigned_agent_id": None},
            {"assigned_adapter": "a2a.outbound"},
            {"input_refs": []},
        )

        for updates in invalid_shapes:
            with self.subTest(updates=updates):
                repository.work_units = [
                    build_work_unit(
                        **{
                            "id": "wu-fork",
                            "kind": "mission.fork",
                            "status": "LEASED",
                            "attempt": 1,
                            "assigned_agent_id": "reviewer",
                            "assigned_adapter": "local_codex",
                            "input_refs": [
                                {"id": "artifact-source", "digest": DIGEST}
                            ],
                            "lease": Lease(
                                id="lease-fork",
                                runner_id="user-1",
                                expires_at=(
                                    datetime.now(timezone.utc) + timedelta(minutes=5)
                                ),
                            ),
                            **updates,
                        }
                    )
                ]

                response = client.post(
                    "/api/v1/missions/mis-1/work-units/wu-fork/execution-context",
                    json={"leaseId": "lease-fork"},
                )

                self.assertEqual(response.status_code, 409)
                self.assertEqual(repository.events, [])

    def test_execution_context_rejects_invalid_outbound_root_shape(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
            source=MissionSource(
                type="a2a",
                reference="https://receiver.example.test/a2a",
                external_id="remote-task-1",
            ),
        )
        repository.contract = build_contract()
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )
        invalid_shapes = (
            {"assigned_adapter": "local_codex"},
            {"required_capabilities": ["artifact.write"]},
            {"parent_work_unit_id": "wu-parent"},
        )

        for updates in invalid_shapes:
            with self.subTest(updates=updates):
                repository.work_units = [
                    build_work_unit(
                        **{
                            "kind": "a2a.delegate",
                            "status": "LEASED",
                            "attempt": 1,
                            "assigned_agent_id": "outbound-dispatcher",
                            "assigned_adapter": "a2a.outbound",
                            "required_capabilities": [
                                "a2a.send",
                                "artifact.write",
                            ],
                            "lease": Lease(
                                id="lease-outbound",
                                runner_id="user-1",
                                expires_at=(
                                    datetime.now(timezone.utc) + timedelta(minutes=5)
                                ),
                            ),
                            **updates,
                        }
                    )
                ]

                response = client.post(
                    "/api/v1/missions/mis-1/work-units/wu-1/execution-context",
                    json={"leaseId": "lease-outbound"},
                )

                self.assertEqual(response.status_code, 409)
                self.assertEqual(repository.events, [])

    def test_execution_context_rejects_wrong_owner_and_expired_lease(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
            source=MissionSource(type="a2a.inbound"),
        )
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                kind="a2a.inbound",
                status="LEASED",
                attempt=1,
                required_capabilities=["a2a.receive"],
                lease=Lease(
                    id="lease-inbound",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        wrong_owner_client = TestClient(
            build_app(
                repository,
                {"id": "admin-1", "name": "Root", "role": "admin"},
            )
        )

        wrong_owner = wrong_owner_client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/execution-context",
            json={"leaseId": "lease-inbound"},
        )

        self.assertEqual(wrong_owner.status_code, 409)
        repository.work_units[0] = build_work_unit(
            kind="a2a.inbound",
            status="LEASED",
            attempt=1,
            required_capabilities=["a2a.receive"],
            lease=Lease(
                id="lease-inbound",
                runner_id="user-1",
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            ),
        )
        expired_client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )
        expired = expired_client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/execution-context",
            json={"leaseId": "lease-inbound"},
        )

        self.assertEqual(expired.status_code, 409)
        self.assertEqual(repository.events, [])

    def test_execution_context_rejects_non_inbound_or_missing_contract(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                status="LEASED",
                attempt=1,
                lease=Lease(
                    id="lease-1",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        non_inbound = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/execution-context",
            json={"leaseId": "lease-1"},
        )

        self.assertEqual(non_inbound.status_code, 409)
        repository.mission = build_mission(
            workspace_id="user-1",
            status="RUNNING",
            source=MissionSource(type="a2a.inbound"),
        )
        repository.work_units[0] = build_work_unit(
            kind="a2a.inbound",
            status="LEASED",
            attempt=1,
            required_capabilities=["a2a.receive"],
            lease=Lease(
                id="lease-1",
                runner_id="user-1",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ),
        )
        repository.contract = None
        missing_contract = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/execution-context",
            json={"leaseId": "lease-1"},
        )

        self.assertEqual(missing_contract.status_code, 409)
        self.assertEqual(repository.events, [])

    def test_start_requires_matching_active_lease(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="LEASED",
                lease=Lease(
                    id="lease-1",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/start",
            json={"leaseId": "wrong-lease"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].status.value, "LEASED")
        self.assertEqual(repository.events, [])

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/start",
            json={"leaseId": "lease-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "RUNNING")
        self.assertIsNotNone(repository.work_units[0].lease)
        self.assertEqual(
            repository.events[-1].event_type, "work_unit.lifecycle.started"
        )

    def test_heartbeat_renews_active_lease_and_appends_event(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        previous_expiry = datetime.now(timezone.utc) + timedelta(seconds=30)
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=previous_expiry,
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/heartbeat",
            json={"leaseId": "lease-running", "leaseSeconds": 300},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "RUNNING")
        self.assertEqual(body["lease"]["id"], "lease-running")
        renewed_expiry = datetime.fromisoformat(body["lease"]["expiresAt"])
        self.assertGreater(renewed_expiry, previous_expiry)
        self.assertEqual(
            repository.events[-1].event_type,
            "work_unit.lifecycle.heartbeat",
        )
        self.assertEqual(repository.events[-1].payload["leaseId"], "lease-running")

    def test_heartbeat_rejects_expired_or_mismatched_lease(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="LEASED",
                lease=Lease(
                    id="lease-expired",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/heartbeat",
            json={"leaseId": "wrong-lease"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].lease.id, "lease-expired")
        self.assertEqual(repository.events, [])

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/heartbeat",
            json={"leaseId": "lease-expired"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].lease.id, "lease-expired")
        self.assertEqual(repository.events, [])

    def test_execution_checkpoint_is_fenced_ordered_and_idempotent(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=1,
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )
        request = {
            "id": "chk-1",
            "leaseId": "lease-running",
            "sequence": 1,
            "phase": "harness.execution.started",
            "iteration": 0,
            "toolCalls": 0,
            "promptTokens": 0,
            "completionTokens": 0,
            "modelCost": 0,
            "terminal": False,
        }
        url = "/api/v1/missions/mis-1/work-units/wu-1/checkpoints"

        wrong_lease = client.post(url, json={**request, "leaseId": "wrong"})
        self.assertEqual(wrong_lease.status_code, 409)
        self.assertEqual(repository.execution_checkpoints, [])
        self.assertEqual(repository.events, [])

        response = client.post(url, json=request)
        duplicate = client.post(url, json=request)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(duplicate.status_code, 201)
        self.assertEqual(response.json(), duplicate.json())
        self.assertEqual(response.json()["attempt"], 1)
        self.assertRegex(response.json()["stateDigest"], r"^sha256:[a-f0-9]{64}$")
        self.assertEqual(len(repository.execution_checkpoints), 1)
        self.assertEqual(len(repository.events), 1)
        self.assertEqual(repository.events[0].event_type, "work_unit.checkpoint.recorded")

        conflicting_id = client.post(
            url,
            json={**request, "iteration": 1},
        )
        self.assertEqual(conflicting_id.status_code, 409)
        self.assertIn("different content", conflicting_id.json()["detail"])

        gap = client.post(url, json={**request, "id": "chk-3", "sequence": 3})
        self.assertEqual(gap.status_code, 409)
        self.assertIn("expected=2, actual=3", gap.json()["detail"])

        terminal = client.post(
            url,
            json={
                **request,
                "id": "chk-2",
                "sequence": 2,
                "phase": "harness.execution.completed",
                "terminal": True,
            },
        )
        self.assertEqual(terminal.status_code, 201)
        after_terminal = client.post(
            url,
            json={**request, "id": "chk-after", "sequence": 3},
        )
        self.assertEqual(after_terminal.status_code, 409)
        self.assertIn("terminal execution checkpoint", after_terminal.json()["detail"])

    def test_expired_lease_is_recovered_to_retrying(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                status="LEASED",
                attempt=2,
                lease=Lease(
                    id="lease-expired",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/work-units/wu-1/recover")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "RETRYING")
        self.assertEqual(response.json()["attempt"], 2)
        self.assertIsNone(repository.work_units[0].lease)
        self.assertEqual(
            repository.events[-1].event_type,
            "work_unit.lifecycle.lease_expired",
        )
        self.assertFalse(repository.events[-1].payload["retryBudgetExhausted"])

    def test_expired_lease_fails_when_retry_budget_is_exhausted(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=3,
                lease=Lease(
                    id="lease-expired",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/work-units/wu-1/recover")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "FAILED")
        self.assertEqual(response.json()["attempt"], 3)
        self.assertIsNone(repository.work_units[0].lease)
        self.assertEqual(repository.mission.status.value, "FAILED")
        self.assertEqual(
            repository.events[-2].event_type,
            "work_unit.lifecycle.lease_expired",
        )
        self.assertTrue(repository.events[-2].payload["retryBudgetExhausted"])
        self.assertEqual(repository.events[-1].event_type, "mission.lifecycle.failed")
        self.assertEqual(
            repository.events[-1].causation_id,
            repository.events[-2].event_id,
        )

    def test_active_lease_cannot_be_recovered(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="LEASED",
                lease=Lease(
                    id="lease-active",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post("/api/v1/missions/mis-1/work-units/wu-1/recover")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].status.value, "LEASED")
        self.assertEqual(repository.events, [])

    def test_runner_registers_and_lists_artifact_metadata_idempotently(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=1,
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )
        payload = {
            "id": "artifact-1",
            "leaseId": "lease-running",
            "kind": "diff",
            "digest": "sha256:" + "a" * 64,
            "contentAddress": "local:sha256/" + "a" * 64,
            "mediaType": "text/x-diff",
            "sizeBytes": 128,
        }

        first = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/artifacts",
            json=payload,
        )
        second = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/artifacts",
            json=payload,
        )
        listed = client.get("/api/v1/missions/mis-1/artifacts")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json()["attempt"], 1)
        self.assertEqual(len(repository.artifacts), 1)
        self.assertEqual(len(repository.events), 1)
        self.assertEqual(
            repository.events[0].event_type,
            "artifact.lifecycle.registered",
        )
        self.assertEqual(repository.events[0].aggregate_type.value, "artifact")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["artifacts"], [first.json()])

    def test_artifact_registration_rejects_invalid_lease_and_content_address(
        self,
    ) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=1,
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )
        payload = {
            "id": "artifact-1",
            "leaseId": "wrong-lease",
            "kind": "diff",
            "digest": "sha256:" + "a" * 64,
            "contentAddress": "local:sha256/" + "a" * 64,
            "mediaType": "text/x-diff",
            "sizeBytes": 128,
        }

        wrong_lease = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/artifacts",
            json=payload,
        )
        missing_digest = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/artifacts",
            json={
                **payload,
                "leaseId": "lease-running",
                "contentAddress": "local:artifacts/artifact-1",
            },
        )

        self.assertEqual(wrong_lease.status_code, 409)
        self.assertEqual(missing_digest.status_code, 409)
        self.assertEqual(repository.artifacts, [])
        self.assertEqual(repository.events, [])

    def test_artifact_registration_rejects_conflicting_id(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=1,
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )
        payload = {
            "id": "artifact-1",
            "leaseId": "lease-running",
            "kind": "diff",
            "digest": "sha256:" + "a" * 64,
            "contentAddress": "local:sha256/" + "a" * 64,
            "mediaType": "text/x-diff",
            "sizeBytes": 128,
        }

        created = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/artifacts",
            json=payload,
        )
        conflict = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/artifacts",
            json={**payload, "sizeBytes": 256},
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(len(repository.artifacts), 1)
        self.assertEqual(len(repository.events), 1)

    def test_complete_moves_running_work_unit_to_verifying(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=1,
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        repository.artifacts = [build_artifact()]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/complete",
            json={
                "leaseId": "lease-running",
                "artifactRefs": [
                    {
                        "id": "artifact-1",
                        "digest": "sha256:" + "a" * 64,
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "VERIFYING")
        self.assertIsNone(repository.work_units[0].lease)
        self.assertEqual(
            repository.events[-1].event_type,
            "work_unit.lifecycle.completed",
        )
        self.assertEqual(
            repository.events[-1].payload["artifactRefs"][0]["id"],
            "artifact-1",
        )

    def test_complete_rejects_unregistered_artifact(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                attempt=1,
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/complete",
            json={
                "leaseId": "lease-running",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(repository.work_units[0].status.value, "RUNNING")
        self.assertIsNotNone(repository.work_units[0].lease)
        self.assertEqual(repository.events, [])

    def test_complete_requires_artifact_refs(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                lease=Lease(
                    id="lease-running",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/complete",
            json={"leaseId": "lease-running", "artifactRefs": []},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(repository.work_units[0].status.value, "RUNNING")
        self.assertEqual(repository.events, [])

    def test_independent_verifier_passes_work_unit_and_mission(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract(
            acceptance_criteria=[
                artifact_set_criterion(required_artifact_kinds=["diff"])
            ]
        )
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        artifact_byte_verifier = FakeArtifactByteVerifier()
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=artifact_byte_verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "configurationDigest": evaluation_policy_digest(
                    repository.contract,
                    repository.work_units[0],
                    repository.artifacts,
                ),
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "All required tests passed.",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["evidence"]["verdict"], "PASS")
        self.assertRegex(
            body["evidence"]["integrityHash"],
            r"^sha256:[a-f0-9]{64}$",
        )
        self.assertEqual(body["workUnit"]["status"], "SUCCEEDED")
        self.assertEqual(body["mission"]["status"], "SUCCEEDED")
        self.assertEqual(
            artifact_byte_verifier.calls,
            [[repository.artifacts[0]]],
        )
        evidence = Evidence.model_validate(body["evidence"])
        self.assertEqual(repository.evidence, [evidence])
        self.assertEqual(
            evidence.integrity_hash,
            recompute_evidence_integrity_hash(repository, evidence),
        )
        self.assertEqual(
            [event.event_type for event in repository.events],
            [
                "evidence.lifecycle.recorded",
                "work_unit.lifecycle.verified",
                "mission.lifecycle.verifying",
                "mission.lifecycle.succeeded",
            ],
        )
        listed = client.get("/api/v1/missions/mis-1/evidence")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["evidence"], [body["evidence"]])

    def test_pass_without_applicable_policy_fails_before_artifact_io(self) -> None:
        for configuration, reason in (
            (None, "no_applicable_policy"),
            (
                {
                    "evaluator": "model-judge.v1",
                    "workUnitKinds": ["code_change"],
                    "minimumArtifacts": 1,
                    "requiredArtifactKinds": [],
                },
                "unsupported_evaluator",
            ),
        ):
            with self.subTest(reason=reason):
                repository = FakeMissionRepository()
                repository.mission = build_mission(
                    workspace_id="verifier-1",
                    status="RUNNING",
                )
                criterion: dict[str, object] = {
                    "id": "tests",
                    "kind": "test",
                    "description": "Tests pass",
                    "required": True,
                }
                if configuration is not None:
                    criterion["configuration"] = configuration
                repository.contract = build_contract(acceptance_criteria=[criterion])
                repository.work_units = [build_work_unit(status="VERIFYING")]
                repository.artifacts = [build_artifact()]
                verifier = FakeArtifactByteVerifier()
                client = TestClient(
                    build_app(
                        repository,
                        {
                            "id": "verifier-1",
                            "name": "Verifier",
                            "role": "verifier",
                        },
                        artifact_byte_verifier=verifier,
                    )
                )

                response = client.post(
                    "/api/v1/missions/mis-1/work-units/wu-1/verify",
                    json={
                        "criterionId": "tests",
                        "verifierId": "verifier-1",
                        "verifierVersion": "9.0",
                        "configurationDigest": "sha256:" + "c" * 64,
                        "verdict": "PASS",
                        "artifactRefs": [
                            {
                                "id": "artifact-1",
                                "digest": "sha256:" + "a" * 64,
                            }
                        ],
                        "summary": "Must not be accepted.",
                        "integrityHash": "sha256:" + "b" * 64,
                    },
                )

                self.assertEqual(response.status_code, 409)
                self.assertIn(reason, response.json()["detail"])
                self.assertEqual(verifier.calls, [])
                self.assertEqual(repository.evidence, [])
                self.assertEqual(repository.events, [])

    def test_pass_must_match_policy_criterion_and_digest(self) -> None:
        for criterion_id, configuration_digest, expected_detail in (
            (
                "security",
                "sha256:" + "c" * 64,
                "criterion does not match",
            ),
            ("tests", None, "requires the evaluation policy configuration digest"),
            (
                "tests",
                "sha256:" + "c" * 64,
                "configuration digest does not match",
            ),
        ):
            with self.subTest(expected_detail=expected_detail):
                repository = FakeMissionRepository()
                repository.mission = build_mission(
                    workspace_id="verifier-1",
                    status="RUNNING",
                )
                repository.contract = build_contract(
                    acceptance_criteria=[
                        artifact_set_criterion(required_artifact_kinds=["diff"]),
                        artifact_set_criterion(
                            criterion_id="security",
                            work_unit_kind="other",
                        ),
                    ]
                )
                repository.work_units = [build_work_unit(status="VERIFYING")]
                repository.artifacts = [build_artifact()]
                verifier = FakeArtifactByteVerifier()
                client = TestClient(
                    build_app(
                        repository,
                        {
                            "id": "verifier-1",
                            "name": "Verifier",
                            "role": "verifier",
                        },
                        artifact_byte_verifier=verifier,
                    )
                )
                request = {
                    "criterionId": criterion_id,
                    "verifierId": "verifier-1",
                    "verifierVersion": "9.0",
                    "verdict": "PASS",
                    "artifactRefs": [
                        {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                    ],
                    "summary": "Must not be accepted.",
                    "integrityHash": "sha256:" + "b" * 64,
                }
                if configuration_digest is not None:
                    request["configurationDigest"] = configuration_digest

                response = client.post(
                    "/api/v1/missions/mis-1/work-units/wu-1/verify",
                    json=request,
                )

                self.assertEqual(response.status_code, 409)
                self.assertIn(expected_detail, response.json()["detail"])
                self.assertEqual(verifier.calls, [])
                self.assertEqual(repository.evidence, [])
                self.assertEqual(repository.events, [])

    def test_pass_policy_is_revalidated_after_artifact_io(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract(
            acceptance_criteria=[
                artifact_set_criterion(required_artifact_kinds=["diff"])
            ]
        )
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        configuration_digest = evaluation_policy_digest(
            repository.contract,
            repository.work_units[0],
            repository.artifacts,
        )

        def replace_contract(_: list[Artifact]) -> None:
            repository.contract = build_contract()

        verifier = FakeArtifactByteVerifier(on_verify=replace_contract)
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "configurationDigest": configuration_digest,
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "The policy changed during verification.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("no_applicable_policy", response.json()["detail"])
        self.assertEqual(verifier.calls, [[build_artifact()]])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_verifier_discovers_only_minimal_current_attempt_context(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.contract = build_contract(governance={"decisionTimeoutSeconds": 900})
        repository.work_units = [build_work_unit(status="VERIFYING", attempt=2)]
        repository.artifacts = [
            build_artifact(id="artifact-old", attempt=1),
            build_artifact(id="artifact-current", attempt=2),
        ]
        grant_authorizer = FakeVerifierWorkspaceGrantAuthorizer(
            {("workspace-1", "verifier-1")}
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                verifier_workspace_grant_authorizer=grant_authorizer,
            )
        )

        response = client.post(
            "/api/v1/missions/verification-work-items/discover",
            json={"workspaceId": "workspace-1"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["discoveryStatus"], "ready")
        context = body["verificationContext"]
        self.assertEqual(context["version"], 3)
        self.assertEqual(
            context["mission"],
            {
                "id": "mis-1",
                "title": repository.mission.title,
                "objective": repository.mission.objective,
            },
        )
        self.assertEqual(
            set(context["contract"]),
            {"id", "version", "acceptanceCriteria"},
        )
        self.assertEqual(
            set(context["workUnit"]),
            {"id", "kind", "inputRefs", "expectedOutputs", "status", "attempt"},
        )
        self.assertEqual(context["workUnit"]["status"], "VERIFYING")
        self.assertEqual(
            [artifact["id"] for artifact in context["artifacts"]],
            ["artifact-current"],
        )
        self.assertNotIn("createdBy", context["artifacts"][0])
        self.assertNotIn("source", context["mission"])
        self.assertNotIn("repositoryScopes", context["contract"])
        self.assertEqual(
            context["evaluationPolicy"],
            {
                "status": "inconclusive",
                "reasonCode": "no_applicable_policy",
                "criterionIds": ["tests"],
            },
        )
        self.assertEqual(
            repository.verification_candidate_calls,
            ["workspace-1"],
        )
        self.assertEqual(
            repository.work_unit_artifact_calls,
            [("mis-1", "wu-1", 2, 201)],
        )
        self.assertEqual(repository.list_mission_calls, [])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.mission.status.value, "WAITING_DECISION")
        self.assertEqual(len(repository.decisions), 1)
        decision = repository.decisions[0]
        self.assertEqual(decision.work_unit_id, "wu-1")
        self.assertEqual(decision.attempt, 2)
        self.assertEqual(decision.reason_code, "no_applicable_policy")
        self.assertEqual(decision.criterion_ids, ("tests",))
        self.assertEqual(
            [option.value for option in decision.options],
            ["RETRY_WORK_UNIT", "FAIL_MISSION"],
        )
        self.assertEqual(decision.recommended_option.value, "FAIL_MISSION")
        self.assertEqual(
            decision.expires_at,
            decision.requested_at + timedelta(minutes=15),
        )
        self.assertEqual(
            [event.event_type for event in repository.events],
            [
                "decision.lifecycle.requested",
                "mission.lifecycle.waiting_decision",
            ],
        )
        self.assertEqual(repository.events[1].causation_id, repository.events[0].event_id)

        repeated = client.post(
            "/api/v1/missions/verification-work-items/discover",
            json={"workspaceId": "workspace-1"},
        )

        self.assertEqual(
            repeated.json(),
            {"discoveryStatus": "idle", "verificationContext": None},
        )
        self.assertEqual(len(repository.decisions), 1)
        self.assertEqual(len(repository.events), 2)

    def test_human_resolves_verification_decision_with_fenced_transitions(
        self,
    ) -> None:
        for resolution, expected_work_unit, expected_mission in (
            ("RETRY_WORK_UNIT", "RETRYING", "RUNNING"),
            ("FAIL_MISSION", "FAILED", "FAILED"),
        ):
            with self.subTest(resolution=resolution):
                repository = FakeMissionRepository()
                repository.mission = build_mission(
                    workspace_id="workspace-1",
                    status="RUNNING",
                )
                repository.contract = build_contract()
                repository.work_units = [
                    build_work_unit(status="VERIFYING", attempt=1)
                ]
                repository.artifacts = [build_artifact()]
                verifier_client = TestClient(
                    build_app(
                        repository,
                        {
                            "id": "verifier-1",
                            "name": "Verifier",
                            "role": "verifier",
                        },
                        verifier_workspace_grant_authorizer=(
                            FakeVerifierWorkspaceGrantAuthorizer(
                                {("workspace-1", "verifier-1")}
                            )
                        ),
                    )
                )
                discovered = verifier_client.post(
                    "/api/v1/missions/verification-work-items/discover",
                    json={"workspaceId": "workspace-1"},
                )
                self.assertEqual(discovered.status_code, 200)
                decision = repository.decisions[0]
                human_client = TestClient(
                    build_app(
                        repository,
                        {"id": "workspace-1", "name": "Ada", "role": "developer"},
                    )
                )

                listed = human_client.get("/api/v1/missions/mis-1/decisions")
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(listed.json()["decisions"][0]["id"], decision.id)
                stale = human_client.post(
                    f"/api/v1/missions/mis-1/decisions/{decision.id}/resolve",
                    json={
                        "expectedVersion": 2,
                        "resolution": resolution,
                        "rationale": "Resolve the blocked verification path.",
                    },
                )
                self.assertEqual(stale.status_code, 409)
                self.assertIn("version conflict", stale.json()["detail"])
                self.assertEqual(repository.mission.status.value, "WAITING_DECISION")

                resolved = human_client.post(
                    f"/api/v1/missions/mis-1/decisions/{decision.id}/resolve",
                    json={
                        "expectedVersion": 1,
                        "resolution": resolution,
                        "rationale": "Resolve the blocked verification path.",
                    },
                )

                self.assertEqual(resolved.status_code, 200)
                self.assertEqual(resolved.json()["decision"]["status"], "RESOLVED")
                self.assertEqual(resolved.json()["decision"]["version"], 2)
                self.assertEqual(
                    resolved.json()["workUnit"]["status"], expected_work_unit
                )
                self.assertEqual(resolved.json()["mission"]["status"], expected_mission)
                self.assertEqual(repository.evidence, [])
                self.assertEqual(
                    [event.event_type for event in repository.events],
                    [
                        "decision.lifecycle.requested",
                        "mission.lifecycle.waiting_decision",
                        "decision.lifecycle.resolved",
                        "work_unit.lifecycle.decision_resolved",
                        "mission.lifecycle.decision_resolved",
                    ],
                )
                repeated = human_client.post(
                    f"/api/v1/missions/mis-1/decisions/{decision.id}/resolve",
                    json={
                        "expectedVersion": 1,
                        "resolution": resolution,
                        "rationale": "Must not resolve twice.",
                    },
                )
                self.assertEqual(repeated.status_code, 409)
                self.assertIn("already resolved", repeated.json()["detail"])

    def test_ready_verification_policy_does_not_open_a_decision(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.contract = build_contract(
            acceptance_criteria=[artifact_set_criterion()]
        )
        repository.work_units = [build_work_unit(status="VERIFYING", attempt=1)]
        repository.artifacts = [build_artifact()]
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                verifier_workspace_grant_authorizer=(
                    FakeVerifierWorkspaceGrantAuthorizer(
                        {("workspace-1", "verifier-1")}
                    )
                ),
            )
        )

        response = client.post(
            "/api/v1/missions/verification-work-items/discover",
            json={"workspaceId": "workspace-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["verificationContext"]["evaluationPolicy"]["status"],
            "ready",
        )
        self.assertEqual(repository.mission.status.value, "RUNNING")
        self.assertEqual(repository.decisions, [])
        self.assertEqual(repository.events, [])

    def test_decision_cannot_retry_after_budget_exhaustion(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.contract = build_contract(
            budgets={"timeSeconds": 3600, "modelCost": 10, "retries": 0}
        )
        repository.work_units = [build_work_unit(status="VERIFYING", attempt=1)]
        repository.artifacts = [build_artifact()]
        verifier_client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                verifier_workspace_grant_authorizer=(
                    FakeVerifierWorkspaceGrantAuthorizer(
                        {("workspace-1", "verifier-1")}
                    )
                ),
            )
        )
        verifier_client.post(
            "/api/v1/missions/verification-work-items/discover",
            json={"workspaceId": "workspace-1"},
        )
        decision = repository.decisions[0]
        self.assertEqual(
            [option.value for option in decision.options],
            ["FAIL_MISSION"],
        )
        human_client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
            )
        )

        response = human_client.post(
            f"/api/v1/missions/mis-1/decisions/{decision.id}/resolve",
            json={
                "expectedVersion": 1,
                "resolution": "RETRY_WORK_UNIT",
                "rationale": "Must not bypass the exhausted retry budget.",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("not offered", response.json()["detail"])
        self.assertEqual(repository.mission.status.value, "WAITING_DECISION")
        self.assertEqual(repository.work_units[0].status.value, "VERIFYING")
        self.assertEqual(repository.decisions[0].status.value, "PENDING")

    def test_verifier_cannot_resolve_a_mission_decision(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="WAITING_DECISION",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING", attempt=1)]
        repository.decisions = [build_decision()]
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/decisions/dec-1/resolve",
            json={
                "expectedVersion": 1,
                "resolution": "FAIL_MISSION",
                "rationale": "Verifier must not control this Decision.",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(repository.decisions[0].status.value, "PENDING")
        self.assertEqual(repository.mission.status.value, "WAITING_DECISION")

    def test_verifier_discovery_denial_precedes_mission_reads(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="workspace-1",
            status="RUNNING",
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
            )
        )

        response = client.post(
            "/api/v1/missions/verification-work-items/discover",
            json={"workspaceId": "workspace-1"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "Verifier workspace grant required",
        )
        self.assertEqual(repository.verification_candidate_calls, [])
        self.assertEqual(repository.work_unit_artifact_calls, [])

    def test_verifier_discovery_returns_idle_without_listing_missions(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.work_units = [build_work_unit(status="SUCCEEDED", attempt=1)]
        grant_authorizer = FakeVerifierWorkspaceGrantAuthorizer(
            {("workspace-1", "verifier-1")}
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                verifier_workspace_grant_authorizer=grant_authorizer,
            )
        )

        response = client.post(
            "/api/v1/missions/verification-work-items/discover",
            json={"workspaceId": "workspace-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"discoveryStatus": "idle", "verificationContext": None},
        )
        self.assertEqual(repository.list_mission_calls, [])
        self.assertEqual(repository.work_unit_artifact_calls, [])

    def test_verifier_discovery_fails_closed_without_current_artifacts(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING", attempt=2)]
        repository.artifacts = [build_artifact(attempt=1)]
        grant_authorizer = FakeVerifierWorkspaceGrantAuthorizer(
            {("workspace-1", "verifier-1")}
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                verifier_workspace_grant_authorizer=grant_authorizer,
            )
        )

        response = client.post(
            "/api/v1/missions/verification-work-items/discover",
            json={"workspaceId": "workspace-1"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("current-attempt Artifacts", response.json()["detail"])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_verifier_discovery_rejects_oversized_artifact_context(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING", attempt=1)]
        repository.artifacts = [
            build_artifact(id=f"artifact-{index:03d}") for index in range(201)
        ]
        grant_authorizer = FakeVerifierWorkspaceGrantAuthorizer(
            {("workspace-1", "verifier-1")}
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                verifier_workspace_grant_authorizer=grant_authorizer,
            )
        )

        response = client.post(
            "/api/v1/missions/verification-work-items/discover",
            json={"workspaceId": "workspace-1"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            "verification Artifact count exceeds 200",
        )
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_explicit_verifier_grant_authorizes_another_workspace(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.contract = build_contract(
            acceptance_criteria=[
                artifact_set_criterion(required_artifact_kinds=["diff"])
            ]
        )
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        grant_authorizer = FakeVerifierWorkspaceGrantAuthorizer(
            {("workspace-1", "verifier-1")}
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                verifier_workspace_grant_authorizer=grant_authorizer,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "configurationDigest": evaluation_policy_digest(
                    repository.contract,
                    repository.work_units[0],
                    repository.artifacts,
                ),
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "Independent workspace verification passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workUnit"]["status"], "SUCCEEDED")
        self.assertEqual(response.json()["mission"]["status"], "SUCCEEDED")
        self.assertNotEqual(
            response.json()["evidence"]["integrityHash"],
            "sha256:" + "b" * 64,
        )
        self.assertEqual(grant_authorizer.calls, [("workspace-1", "verifier-1")])

    def test_verifier_grant_denial_precedes_artifact_io_and_state_change(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        artifact_verifier = FakeArtifactByteVerifier()
        grant_authorizer = FakeVerifierWorkspaceGrantAuthorizer()
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=artifact_verifier,
                verifier_workspace_grant_authorizer=grant_authorizer,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "Must not be recorded.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Verifier workspace grant required")
        self.assertEqual(artifact_verifier.calls, [])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])
        self.assertEqual(repository.work_units[0].status.value, "VERIFYING")

    def test_unavailable_verifier_grant_store_fails_closed(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="workspace-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        grant_authorizer = FakeVerifierWorkspaceGrantAuthorizer(
            error=VerifierWorkspaceGrantUnavailableError("database unavailable")
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                verifier_workspace_grant_authorizer=grant_authorizer,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "INCONCLUSIVE",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "Authorization unavailable.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Verifier workspace authorization is unavailable",
        )
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_evidence_rejects_artifact_from_another_work_unit(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(id="wu-1", status="VERIFYING"),
            build_work_unit(id="wu-2", status="VERIFYING"),
        ]
        repository.artifacts = [build_artifact(work_unit_id="wu-2")]
        verifier = FakeArtifactByteVerifier()
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "Wrong WorkUnit artifact.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("another work unit", response.json()["detail"])
        self.assertEqual(verifier.calls, [])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_delegated_work_unit_closes_artifact_evidence_and_mission(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract(
            acceptance_criteria=[
                artifact_set_criterion(required_artifact_kinds=["test-result"])
            ]
        )
        repository.work_units = [
            build_work_unit(id="wu-parent", status="SUCCEEDED"),
            build_work_unit(
                id="wu-child",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            ),
        ]
        runner_client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )

        claimed = runner_client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={"agentId": "reviewer", "adapterType": "local_codex"},
        )
        self.assertEqual(claimed.status_code, 200)
        lease_id = claimed.json()["workUnit"]["lease"]["id"]
        started = runner_client.post(
            "/api/v1/missions/mis-1/work-units/wu-child/start",
            json={"leaseId": lease_id},
        )
        self.assertEqual(started.status_code, 200)

        digest = "sha256:" + "a" * 64
        registered = runner_client.post(
            "/api/v1/missions/mis-1/work-units/wu-child/artifacts",
            json={
                "id": "artifact-child",
                "leaseId": lease_id,
                "kind": "test-result",
                "digest": digest,
                "contentAddress": "local:sha256/" + "a" * 64,
                "mediaType": "text/plain",
                "sizeBytes": 12,
            },
        )
        self.assertEqual(registered.status_code, 201)
        completed = runner_client.post(
            "/api/v1/missions/mis-1/work-units/wu-child/complete",
            json={
                "leaseId": lease_id,
                "artifactRefs": [{"id": "artifact-child", "digest": digest}],
            },
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "VERIFYING")

        verifier = FakeArtifactByteVerifier()
        verifier_client = TestClient(
            build_app(
                repository,
                {"id": "admin-1", "name": "Verifier", "role": "admin"},
                artifact_byte_verifier=verifier,
            )
        )
        evidence = verifier_client.post(
            "/api/v1/missions/mis-1/work-units/wu-child/verify",
            json={
                "criterionId": "tests",
                "verifierId": "admin-1",
                "verifierVersion": "1.0",
                "configurationDigest": evaluation_policy_digest(
                    repository.contract,
                    repository.work_units[1],
                    repository.artifacts,
                ),
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-child", "digest": digest}],
                "summary": "Delegated artifact verified.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(evidence.status_code, 200)
        self.assertEqual(evidence.json()["workUnit"]["status"], "SUCCEEDED")
        self.assertEqual(evidence.json()["evidence"]["workUnitId"], "wu-child")
        self.assertEqual(evidence.json()["mission"]["status"], "SUCCEEDED")
        self.assertEqual(repository.artifacts[0].work_unit_id, "wu-child")

    def test_expired_delegated_claim_recovers_and_can_be_reclaimed(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(id="wu-parent", status="SUCCEEDED"),
            build_work_unit(
                id="wu-child",
                parent_work_unit_id="wu-parent",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            ),
        ]
        runner_client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Runner", "role": "runner"},
            )
        )
        claimed = runner_client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={"agentId": "reviewer", "adapterType": "local_codex"},
        )
        self.assertEqual(claimed.status_code, 200)
        lease_id = claimed.json()["workUnit"]["lease"]["id"]
        child = repository.work_units[1]
        repository.work_units[1] = child.model_copy(
            update={
                "lease": Lease(
                    id=lease_id,
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                )
            }
        )

        recovered = runner_client.post(
            "/api/v1/missions/mis-1/work-units/wu-child/recover"
        )
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["status"], "RETRYING")
        reclaimed = runner_client.post(
            "/api/v1/missions/mis-1/work-unit-claims",
            json={"agentId": "reviewer", "adapterType": "local_codex"},
        )
        self.assertEqual(reclaimed.status_code, 200)
        self.assertEqual(reclaimed.json()["workUnit"]["status"], "LEASED")
        self.assertEqual(reclaimed.json()["workUnit"]["attempt"], 2)

    def test_unavailable_artifact_bytes_fail_without_state_changes(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract(
            acceptance_criteria=[
                artifact_set_criterion(required_artifact_kinds=["diff"])
            ]
        )
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        initial_mission = repository.mission
        initial_work_unit = repository.work_units[0]
        verifier = FakeArtifactByteVerifier(
            error=ArtifactBytesUnavailableError("artifact store unavailable")
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "configurationDigest": evaluation_policy_digest(
                    repository.contract,
                    repository.work_units[0],
                    repository.artifacts,
                ),
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.json()["detail"], "artifact store unavailable")
        self.assertEqual(repository.mission, initial_mission)
        self.assertEqual(repository.work_units, [initial_work_unit])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_incomplete_byte_evaluation_cannot_admit_pass(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract(
            acceptance_criteria=[
                artifact_set_criterion(required_artifact_kinds=["diff"])
            ]
        )
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        initial_mission = repository.mission
        initial_work_unit = repository.work_units[0]
        verifier = FakeArtifactByteVerifier(results=[])
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "configurationDigest": evaluation_policy_digest(
                    repository.contract,
                    repository.work_units[0],
                    repository.artifacts,
                ),
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "Must not pass without complete byte evaluation.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("do not exactly match", response.json()["detail"])
        self.assertEqual(verifier.calls, [[repository.artifacts[0]]])
        self.assertEqual(repository.mission, initial_mission)
        self.assertEqual(repository.work_units, [initial_work_unit])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_artifact_digest_mismatch_fails_without_state_changes(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract(
            acceptance_criteria=[
                artifact_set_criterion(required_artifact_kinds=["diff"])
            ]
        )
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        initial_mission = repository.mission
        initial_work_unit = repository.work_units[0]
        verifier = FakeArtifactByteVerifier(
            error=ArtifactIntegrityError("artifact byte digest does not match")
        )
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "configurationDigest": evaluation_policy_digest(
                    repository.contract,
                    repository.work_units[0],
                    repository.artifacts,
                ),
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            "artifact byte digest does not match",
        )
        self.assertEqual(repository.mission, initial_mission)
        self.assertEqual(repository.work_units, [initial_work_unit])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_artifact_metadata_is_revalidated_after_byte_verification(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract(
            acceptance_criteria=[
                artifact_set_criterion(required_artifact_kinds=["diff"])
            ]
        )
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        initial_mission = repository.mission
        initial_work_unit = repository.work_units[0]

        def replace_artifact(_: list[Artifact]) -> None:
            repository.artifacts[0] = build_artifact(size_bytes=129)

        verifier = FakeArtifactByteVerifier(on_verify=replace_artifact)
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "configurationDigest": evaluation_policy_digest(
                    repository.contract,
                    repository.work_units[0],
                    repository.artifacts,
                ),
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            "artifact metadata changed during byte verification",
        )
        self.assertEqual(repository.mission, initial_mission)
        self.assertEqual(repository.work_units, [initial_work_unit])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_mission_success_uses_unbounded_passed_criterion_projection(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract(
            acceptance_criteria=[
                artifact_set_criterion(required_artifact_kinds=["diff"])
            ]
        )
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        repository.evidence = [
            build_evidence(
                id=f"evd-history-{index}",
                criterion_id=f"legacy-{index}",
            )
            for index in range(200)
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "configurationDigest": evaluation_policy_digest(
                    repository.contract,
                    repository.work_units[0],
                    repository.artifacts,
                ),
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mission"]["status"], "SUCCEEDED")
        self.assertEqual(len(repository.evidence), 201)

    def test_mission_waits_for_all_required_criteria(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract(
            acceptance_criteria=[
                artifact_set_criterion(
                    criterion_id="tests",
                    work_unit_kind="test_verification",
                    required_artifact_kinds=["diff"],
                ),
                artifact_set_criterion(
                    criterion_id="security",
                    criterion_kind="security",
                    work_unit_kind="security_verification",
                    required_artifact_kinds=["diff"],
                ),
            ]
        )
        repository.work_units = [
            build_work_unit(
                id="wu-tests",
                kind="test_verification",
                status="VERIFYING",
            ),
            build_work_unit(
                id="wu-security",
                kind="security_verification",
                status="VERIFYING",
            ),
        ]
        tests_digest = "sha256:" + "a" * 64
        security_digest = "sha256:" + "c" * 64
        repository.artifacts = [
            build_artifact(
                id="artifact-tests",
                work_unit_id="wu-tests",
                digest=tests_digest,
                content_address="local:sha256/" + "a" * 64,
            ),
            build_artifact(
                id="artifact-security",
                work_unit_id="wu-security",
                digest=security_digest,
                content_address="local:sha256/" + "c" * 64,
            ),
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
            )
        )
        request = {
            "verifierId": "verifier-1",
            "verifierVersion": "9.0",
            "verdict": "PASS",
            "summary": "Verification passed.",
            "integrityHash": "sha256:" + "b" * 64,
        }

        first = client.post(
            "/api/v1/missions/mis-1/work-units/wu-tests/verify",
            json={
                **request,
                "criterionId": "tests",
                "configurationDigest": evaluation_policy_digest(
                    repository.contract,
                    repository.work_units[0],
                    [repository.artifacts[0]],
                ),
                "artifactRefs": [{"id": "artifact-tests", "digest": tests_digest}],
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["workUnit"]["status"], "SUCCEEDED")
        self.assertEqual(first.json()["mission"]["status"], "RUNNING")

        second = client.post(
            "/api/v1/missions/mis-1/work-units/wu-security/verify",
            json={
                **request,
                "criterionId": "security",
                "configurationDigest": evaluation_policy_digest(
                    repository.contract,
                    repository.work_units[1],
                    [repository.artifacts[1]],
                ),
                "artifactRefs": [
                    {"id": "artifact-security", "digest": security_digest}
                ],
            },
        )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["workUnit"]["status"], "SUCCEEDED")
        self.assertEqual(second.json()["mission"]["status"], "SUCCEEDED")

    def test_inconclusive_evidence_does_not_claim_success(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "INCONCLUSIVE",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "The test environment was unavailable.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Mission Decision", response.json()["detail"])
        self.assertEqual(repository.work_units[0].status.value, "VERIFYING")
        self.assertEqual(repository.mission.status.value, "RUNNING")
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_non_contract_evidence_criterion_fails_before_artifact_io(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        artifact_byte_verifier = FakeArtifactByteVerifier()
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=artifact_byte_verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "not-in-contract",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "INCONCLUSIVE",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "Must not be admitted.",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("not part", response.json()["detail"])
        self.assertEqual(artifact_byte_verifier.calls, [])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_waiting_decision_rejects_evidence_before_artifact_io(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="WAITING_DECISION",
        )
        repository.contract = build_contract(
            acceptance_criteria=[artifact_set_criterion()]
        )
        repository.work_units = [build_work_unit(status="VERIFYING", attempt=1)]
        repository.artifacts = [build_artifact()]
        artifact_byte_verifier = FakeArtifactByteVerifier()
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
                artifact_byte_verifier=artifact_byte_verifier,
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "configurationDigest": evaluation_policy_digest(
                    repository.contract,
                    repository.work_units[0],
                    repository.artifacts,
                ),
                "verdict": "PASS",
                "artifactRefs": [
                    {"id": "artifact-1", "digest": "sha256:" + "a" * 64}
                ],
                "summary": "Must wait for the human Decision.",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("RUNNING or VERIFYING", response.json()["detail"])
        self.assertEqual(artifact_byte_verifier.calls, [])
        self.assertEqual(repository.evidence, [])
        self.assertEqual(repository.events, [])

    def test_failed_evidence_fails_work_unit_and_mission(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        repository.artifacts = [build_artifact()]
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "verifier-1",
                "verifierVersion": "9.0",
                "verdict": "FAIL",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "A required test failed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workUnit"]["status"], "FAILED")
        self.assertEqual(response.json()["mission"]["status"], "FAILED")
        self.assertEqual(len(repository.evidence), 1)
        self.assertEqual(
            [event.event_type for event in repository.events],
            [
                "evidence.lifecycle.recorded",
                "work_unit.lifecycle.verification_failed",
                "mission.lifecycle.failed",
            ],
        )
        self.assertEqual(repository.events[-1].payload["workUnitId"], "wu-1")
        self.assertEqual(
            repository.events[-1].causation_id,
            repository.events[-2].event_id,
        )

    def test_non_verifier_cannot_record_evidence(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "pytest",
                "verifierVersion": "9.0",
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(repository.events, [])

    def test_verifier_cannot_impersonate_another_verifier(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="verifier-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="VERIFYING")]
        client = TestClient(
            build_app(
                repository,
                {"id": "verifier-1", "name": "Verifier", "role": "verifier"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/verify",
            json={
                "criterionId": "tests",
                "verifierId": "other-verifier",
                "verifierVersion": "9.0",
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": "sha256:" + "a" * 64}],
                "summary": "All required tests passed.",
                "integrityHash": "sha256:" + "b" * 64,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(repository.events, [])

    def test_fail_and_retry_require_lease_and_respect_retry_budget(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.contract = build_contract()
        repository.work_units = [
            build_work_unit(
                status="RUNNING",
                lease=Lease(
                    id="lease-fail",
                    runner_id="user-1",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                ),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "user-1", "name": "Ada", "role": "developer"},
            )
        )

        failed = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/fail",
            json={"leaseId": "lease-fail", "reason": "runner error"},
        )
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()["status"], "FAILED")
        self.assertEqual(repository.mission.status.value, "FAILED")
        self.assertEqual(repository.events[-2].payload["reason"], "runner error")
        self.assertEqual(repository.events[-1].event_type, "mission.lifecycle.failed")
        self.assertEqual(repository.events[-1].payload["reason"], "runner error")

        repository.mission = build_mission(workspace_id="user-1", status="RUNNING")
        repository.work_units[0] = build_work_unit(
            status="RUNNING",
            attempt=3,
            lease=Lease(
                id="lease-exhausted",
                runner_id="user-1",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ),
        )
        before_events = len(repository.events)
        exhausted = client.post(
            "/api/v1/missions/mis-1/work-units/wu-1/retry",
            json={"leaseId": "lease-exhausted", "reason": "retry"},
        )
        self.assertEqual(exhausted.status_code, 409)
        self.assertEqual(len(repository.events), before_events)


class ContractRevisionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_human_actor_is_rejected_before_repository_access(self) -> None:
        class RepositoryThatMustNotBeCalled(FakeMissionRepository):
            @asynccontextmanager
            async def transaction(self):
                raise AssertionError("actor validation must precede repository access")
                yield self

        with self.assertRaisesRegex(ValueError, "only human actors"):
            await MissionService(RepositoryThatMustNotBeCalled()).revise_contract(
                "mis-1",
                expected_version=1,
                contract=build_contract(version=2),
                reason="Runner must not revise policy.",
                actor=ActorRef(type="runner", id="runner-1"),
            )


class DecisionExpiryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_expiry_fails_closed_once_with_causal_events(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(status="WAITING_DECISION")
        repository.work_units = [build_work_unit(status="VERIFYING", attempt=1)]
        decision = build_decision(
            requested_at=datetime(2026, 8, 16, 8, tzinfo=timezone.utc),
            expires_at=datetime(2026, 8, 16, 9, tzinfo=timezone.utc),
        )
        repository.decisions = [decision]
        repository.events = [
            build_event(
                event_id="evt-decision-requested",
                aggregate_type="decision",
                aggregate_id=decision.id,
                event_type="decision.lifecycle.requested",
                payload=decision.to_public_dict(),
            ),
            build_event(
                event_id="evt-mission-waiting",
                aggregate_id=repository.mission.id,
                event_type="mission.lifecycle.waiting_decision",
                payload={
                    "previousStatus": "RUNNING",
                    "status": "WAITING_DECISION",
                    "decisionId": decision.id,
                    "workUnitId": "wu-1",
                },
            ),
        ]
        service = MissionService(repository)
        occurred_at = datetime(2026, 8, 16, 10, tzinfo=timezone.utc)

        outcome = await service.expire_next_decision(occurred_at=occurred_at)

        self.assertTrue(outcome.expired)
        self.assertEqual(repository.decisions[0].status, DecisionStatus.EXPIRED)
        self.assertEqual(repository.decisions[0].version, 2)
        self.assertIsNone(repository.decisions[0].resolution)
        self.assertEqual(repository.decisions[0].resolved_at, occurred_at)
        self.assertEqual(repository.work_units[0].status.value, "FAILED")
        self.assertEqual(repository.mission.status.value, "FAILED")
        expiry_events = repository.events[-3:]
        self.assertEqual(
            [event.event_type for event in expiry_events],
            [
                "decision.lifecycle.expired",
                "work_unit.lifecycle.decision_expired",
                "mission.lifecycle.decision_expired",
            ],
        )
        self.assertEqual(expiry_events[1].causation_id, expiry_events[0].event_id)
        self.assertEqual(expiry_events[2].causation_id, expiry_events[1].event_id)
        self.assertEqual(expiry_events[0].actor.type.value, "service")

        repeated = await service.expire_next_decision(occurred_at=occurred_at)
        self.assertFalse(repeated.expired)
        self.assertEqual(len(repository.events), 5)

    async def test_expiry_time_is_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            await MissionService(FakeMissionRepository()).expire_next_decision(
                occurred_at=datetime(2026, 8, 16)  # noqa: DTZ001
            )


class DecisionExpiryApiTests(unittest.TestCase):
    def test_human_cannot_resolve_decision_after_expiry(self) -> None:
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="workspace-1",
            status="WAITING_DECISION",
        )
        repository.work_units = [build_work_unit(status="VERIFYING", attempt=1)]
        now = datetime.now(timezone.utc)
        repository.decisions = [
            build_decision(
                requested_at=now - timedelta(hours=2),
                expires_at=now - timedelta(hours=1),
            )
        ]
        client = TestClient(
            build_app(
                repository,
                {"id": "workspace-1", "name": "Ada", "role": "developer"},
            )
        )

        response = client.post(
            "/api/v1/missions/mis-1/decisions/dec-1/resolve",
            json={
                "expectedVersion": 1,
                "resolution": "FAIL_MISSION",
                "rationale": "This command arrived after the deadline.",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("expired", response.json()["detail"])
        self.assertEqual(repository.decisions[0].status, DecisionStatus.PENDING)
        self.assertEqual(repository.mission.status.value, "WAITING_DECISION")
        self.assertEqual(repository.work_units[0].status.value, "VERIFYING")
        self.assertEqual(repository.events, [])


if __name__ == "__main__":
    unittest.main()
