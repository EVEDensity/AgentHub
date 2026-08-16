from __future__ import annotations

from datetime import datetime, timezone

from app.domain import (
    AcceptanceCriterion,
    ActorRef,
    Artifact,
    Budgets,
    CapabilityGrant,
    Decision,
    DecisionGate,
    EventEnvelope,
    Evidence,
    ExecutionCheckpoint,
    Mission,
    MissionContract,
    MissionSource,
    RepositoryScope,
    WorkUnit,
)

NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)
DIGEST = "sha256:" + "a" * 64


def build_mission(**updates: object) -> Mission:
    values: dict[str, object] = {
        "id": "mis-1",
        "workspace_id": "workspace-1",
        "title": "Fix issue 42",
        "objective": "Produce a tested pull request.",
        "source": MissionSource(
            type="issue", reference="https://example.test/issues/42"
        ),
        "contract_id": "contract-1",
        "contract_version": 1,
        "status": "READY",
        "plan_version": 0,
        "created_by": ActorRef(type="human", id="user-1"),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return Mission.model_validate(values)


def build_contract(**updates: object) -> MissionContract:
    values: dict[str, object] = {
        "id": "contract-1",
        "version": 1,
        "repository_scopes": [
            RepositoryScope(repository="owner/repo", base_ref="main", paths=["app/**"])
        ],
        "allowed_capabilities": [CapabilityGrant(capability="repository.write")],
        "budgets": Budgets(time_seconds=3600, model_cost=10, retries=2),
        "acceptance_criteria": [
            AcceptanceCriterion(
                id="tests", kind="test", description="Tests pass", required=True
            )
        ],
        "decision_gates": [DecisionGate(id="deploy", trigger="deployment.requested")],
        "forbidden_actions": ["repository.force_push"],
    }
    values.update(updates)
    return MissionContract.model_validate(values)


def build_artifact(**updates: object) -> Artifact:
    values: dict[str, object] = {
        "id": "artifact-1",
        "mission_id": "mis-1",
        "work_unit_id": "wu-1",
        "attempt": 1,
        "kind": "diff",
        "digest": DIGEST,
        "content_address": "local:sha256/" + "a" * 64,
        "media_type": "text/x-diff",
        "size_bytes": 128,
        "created_by": ActorRef(type="human", id="runner-1"),
        "created_at": NOW,
    }
    values.update(updates)
    return Artifact.model_validate(values)


def build_evidence(**updates: object) -> Evidence:
    values: dict[str, object] = {
        "id": "evd-1",
        "mission_id": "mis-1",
        "work_unit_id": "wu-1",
        "criterion_id": "tests",
        "verifier": {"id": "pytest", "version": "9.0"},
        "verdict": "PASS",
        "artifact_refs": [{"id": "artifact-1", "digest": DIGEST}],
        "summary": "All required tests passed.",
        "generated_at": NOW,
        "integrity_hash": "sha256:" + "b" * 64,
    }
    values.update(updates)
    return Evidence.model_validate(values)


def build_decision(**updates: object) -> Decision:
    values: dict[str, object] = {
        "id": "dec-1",
        "mission_id": "mis-1",
        "work_unit_id": "wu-1",
        "attempt": 1,
        "context_digest": "sha256:" + "c" * 64,
        "reason_code": "no_applicable_policy",
        "criterion_ids": ["tests"],
        "options": ["RETRY_WORK_UNIT", "FAIL_MISSION"],
        "recommended_option": "FAIL_MISSION",
        "risk_summary": "Verification policy cannot prove the required criteria.",
        "status": "PENDING",
        "version": 1,
        "requested_by": ActorRef(type="service", id="mission-control"),
        "requested_at": NOW,
    }
    values.update(updates)
    return Decision.model_validate(values)


def build_event(**updates: object) -> EventEnvelope:
    values: dict[str, object] = {
        "event_id": "evt-1",
        "aggregate_type": "mission",
        "aggregate_id": "mis-1",
        "sequence": 1,
        "event_type": "mission.lifecycle.created",
        "actor": ActorRef(type="human", id="user-1"),
        "occurred_at": NOW,
        "correlation_id": "mis-1",
        "payload": {"contractId": "contract-1", "status": "READY"},
        "schema_version": 1,
    }
    values.update(updates)
    return EventEnvelope.model_validate(values)


def build_work_unit(**updates: object) -> WorkUnit:
    values: dict[str, object] = {
        "id": "wu-1",
        "mission_id": "mis-1",
        "kind": "code_change",
        "dependencies": [],
        "input_refs": [],
        "expected_outputs": [{"kind": "diff", "required": True}],
        "required_capabilities": ["repository.write"],
        "status": "PENDING",
        "attempt": 0,
    }
    values.update(updates)
    return WorkUnit.model_validate(values)


def build_execution_checkpoint(**updates: object) -> ExecutionCheckpoint:
    values: dict[str, object] = {
        "id": "chk-1",
        "mission_id": "mis-1",
        "work_unit_id": "wu-1",
        "attempt": 1,
        "sequence": 1,
        "phase": "harness.execution.started",
        "iteration": 0,
        "tool_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "model_cost": 0,
        "terminal": False,
        "state_digest": "sha256:" + "d" * 64,
        "created_by": ActorRef(type="runner", id="runner-1"),
        "created_at": NOW,
    }
    values.update(updates)
    return ExecutionCheckpoint.model_validate(values)
