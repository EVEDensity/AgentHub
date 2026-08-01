from __future__ import annotations

from datetime import datetime, timezone

from app.domain import (
    AcceptanceCriterion,
    ActorRef,
    Budgets,
    CapabilityGrant,
    DecisionGate,
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
