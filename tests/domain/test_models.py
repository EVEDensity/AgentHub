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
    ArtifactRef,
    Evidence,
    Mission,
    VerifierRef,
)
from tests.domain.factories import (
    DIGEST,
    NOW,
    build_contract,
    build_mission,
    build_work_unit,
)


CONTRACT_DIR = Path(__file__).parents[2] / "platform" / "contracts" / "v1"


class DomainModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in CONTRACT_DIR.glob("*.schema.json")
        }
        cls.registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema))
            for schema in cls.schemas.values()
        )

    def assert_matches_schema(self, schema_name: str, model: object) -> None:
        document = model.to_public_dict()
        Draft202012Validator(
            self.schemas[schema_name], registry=self.registry
        ).validate(document)

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

    def test_contract_collections_and_nested_configuration_are_immutable(self) -> None:
        contract = build_contract(
            allowed_capabilities=[
                {"capability": "repository.write", "scope": {"paths": ["app/**"]}}
            ]
        )
        with self.assertRaises(ValidationError):
            contract.forbidden_actions += ("repository.delete",)
        with self.assertRaises(TypeError):
            contract.allowed_capabilities[0].scope["repository"] = "other/repo"
        with self.assertRaises(TypeError):
            contract.allowed_capabilities[0].scope["paths"].append("tests/**")
        self.assertEqual(
            contract.to_public_dict()["allowedCapabilities"][0]["scope"],
            {"paths": ["app/**"]},
        )

    def test_contract_configuration_rejects_non_json_values(self) -> None:
        with self.assertRaisesRegex(ValidationError, "valid JSON value"):
            build_contract(
                allowed_capabilities=[
                    {"capability": "repository.write", "scope": {"paths": {"app/**"}}}
                ]
            )

    def test_contract_rejects_duplicate_domain_keys(self) -> None:
        criterion = AcceptanceCriterion(
            id="tests", kind="test", description="Tests pass", required=True
        )
        with self.assertRaisesRegex(ValidationError, "criterion ids must be unique"):
            build_contract(acceptance_criteria=[criterion, criterion])
        with self.assertRaisesRegex(
            ValidationError, "forbidden actions must be unique"
        ):
            build_contract(
                forbidden_actions=["repository.force_push", "repository.force_push"]
            )

    def test_work_unit_requires_lease_while_executing(self) -> None:
        with self.assertRaisesRegex(
            ValidationError, "RUNNING work unit requires a lease"
        ):
            build_work_unit(status="RUNNING")
        with self.assertRaisesRegex(ValidationError, "cannot depend on itself"):
            build_work_unit(dependencies=["wu-1"])
        with self.assertRaisesRegex(
            ValidationError, "PENDING work unit cannot retain a lease"
        ):
            build_work_unit(
                lease={
                    "id": "lease-1",
                    "runner_id": "runner-1",
                    "expires_at": NOW,
                }
            )

    def test_mission_requires_ordered_timezone_aware_timestamps(self) -> None:
        with self.assertRaises(ValidationError):
            build_mission(
                created_at=datetime(2026, 8, 1), updated_at=datetime(2026, 8, 1)
            )
        with self.assertRaisesRegex(ValidationError, "updated_at cannot be earlier"):
            build_mission(updated_at=datetime(2026, 7, 31, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
