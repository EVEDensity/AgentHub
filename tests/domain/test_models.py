from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing import Registry, Resource

from app.domain import (
    AcceptanceCriterion,
    ActorRef,
    ArtifactRef,
    Budgets,
    CapabilityGrant,
    DecisionGate,
    Evidence,
    Mission,
    MissionContract,
    MissionSource,
    RepositoryScope,
    VerifierRef,
    WorkUnit,
)


NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)
DIGEST = "sha256:" + "a" * 64
CONTRACT_DIR = Path(__file__).parents[2] / "platform" / "contracts" / "v1"


def build_mission(**updates: object) -> Mission:
    values: dict[str, object] = {
        "id": "mis-1",
        "workspace_id": "workspace-1",
        "title": "Fix issue 42",
        "objective": "Produce a tested pull request.",
        "source": MissionSource(type="issue", reference="https://example.test/issues/42"),
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
            AcceptanceCriterion(id="tests", kind="test", description="Tests pass", required=True)
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


class DomainModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in CONTRACT_DIR.glob("*.schema.json")
        }
        cls.registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in cls.schemas.values()
        )

    def assert_matches_schema(self, schema_name: str, model: object) -> None:
        document = model.to_public_dict()
        Draft202012Validator(self.schemas[schema_name], registry=self.registry).validate(document)

    def test_models_serialize_to_public_contracts(self) -> None:
        mission = build_mission()
        contract = build_contract()
        work_unit = build_work_unit()
        evidence = Evidence(
            id="evd-1",
            mission_id="mis-1",
            criterion_id="tests",
            verifier=VerifierRef(id="pytest", version="9.0"),
            verdict="PASS",
            artifact_refs=[ArtifactRef(id="artifact-1", digest=DIGEST)],
            summary="All required tests passed.",
            generated_at=NOW,
            integrity_hash=DIGEST,
        )

        self.assert_matches_schema("mission.schema.json", mission)
        self.assert_matches_schema("mission-contract.schema.json", contract)
        self.assert_matches_schema("work-unit.schema.json", work_unit)
        self.assert_matches_schema("evidence.schema.json", evidence)

    def test_models_accept_public_camel_case_documents(self) -> None:
        document = build_mission().to_public_dict()
        restored = Mission.model_validate(document)
        self.assertEqual(restored.workspace_id, "workspace-1")
        self.assertEqual(restored.contract_id, "contract-1")

    def test_models_are_frozen_and_reject_unknown_fields(self) -> None:
        mission = build_mission()
        with self.assertRaises(ValidationError):
            mission.status = "RUNNING"
        with self.assertRaises(ValidationError):
            build_mission(unknown_field=True)

    def test_contract_rejects_duplicate_domain_keys(self) -> None:
        criterion = AcceptanceCriterion(
            id="tests", kind="test", description="Tests pass", required=True
        )
        with self.assertRaisesRegex(ValidationError, "criterion ids must be unique"):
            build_contract(acceptance_criteria=[criterion, criterion])
        with self.assertRaisesRegex(ValidationError, "forbidden actions must be unique"):
            build_contract(forbidden_actions=["repository.force_push", "repository.force_push"])

    def test_work_unit_requires_lease_while_executing(self) -> None:
        with self.assertRaisesRegex(ValidationError, "RUNNING work unit requires a lease"):
            build_work_unit(status="RUNNING")
        with self.assertRaisesRegex(ValidationError, "cannot depend on itself"):
            build_work_unit(dependencies=["wu-1"])

    def test_mission_requires_ordered_timezone_aware_timestamps(self) -> None:
        with self.assertRaises(ValidationError):
            build_mission(created_at=datetime(2026, 8, 1), updated_at=datetime(2026, 8, 1))
        with self.assertRaisesRegex(ValidationError, "updated_at cannot be earlier"):
            build_mission(updated_at=datetime(2026, 7, 31, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
