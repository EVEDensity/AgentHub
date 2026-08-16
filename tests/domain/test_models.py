from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
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
    build_artifact,
    build_contract,
    build_decision,
    build_event,
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
        artifact = build_artifact()
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
        self.assert_matches_schema("artifact.schema.json", artifact)
        self.assert_matches_schema("evidence.schema.json", evidence)
        self.assert_matches_schema("decision.schema.json", build_decision())

    def test_decision_enforces_pending_and_resolved_lifecycle(self) -> None:
        pending = build_decision()
        resolved = build_decision(
            status="RESOLVED",
            version=2,
            resolution="RETRY_WORK_UNIT",
            rationale="Run a new attempt with corrected Artifact output.",
            resolved_by={"type": "human", "id": "user-1"},
            resolved_at=NOW,
        )

        self.assertEqual(resolved.resolution.value, "RETRY_WORK_UNIT")
        with self.assertRaisesRegex(ValidationError, "cannot carry resolution"):
            build_decision(resolution="FAIL_MISSION")
        with self.assertRaisesRegex(ValidationError, "sorted and unique"):
            build_decision(criterion_ids=["tests", "tests"])
        with self.assertRaisesRegex(ValidationError, "offered by"):
            build_decision(recommended_option="RETRY_WORK_UNIT", options=["FAIL_MISSION"])
        with self.assertRaisesRegex(ValidationError, "resolution metadata"):
            build_decision(status="RESOLVED", version=2)
        cancelled = build_decision(
            status="CANCELLED",
            version=2,
            rationale="Mission was cancelled.",
            resolved_by={"type": "human", "id": "user-1"},
            resolved_at=NOW,
        )
        self.assert_matches_schema("decision.schema.json", cancelled)
        self.assertIsNone(cancelled.resolution)
        expired = build_decision(
            status="EXPIRED",
            version=2,
            requested_at=NOW - timedelta(hours=2),
            expires_at=NOW - timedelta(hours=1),
            rationale="Decision expired before human resolution.",
            resolved_by={"type": "service", "id": "mission-control"},
            resolved_at=NOW,
        )
        self.assert_matches_schema("decision.schema.json", expired)
        self.assertIsNone(expired.resolution)
        with self.assertRaisesRegex(ValidationError, "requires expires_at"):
            build_decision(
                status="EXPIRED",
                version=2,
                rationale="Decision expired before human resolution.",
                resolved_by={"type": "service", "id": "mission-control"},
                resolved_at=NOW,
            )
        self.assertEqual(pending.version, 1)

    def test_event_envelope_uses_public_snake_case_contract(self) -> None:
        event = build_event()
        public = event.to_public_dict()

        self.assert_matches_schema("event-envelope.schema.json", event)
        self.assertEqual(public["event_id"], "evt-1")
        self.assertEqual(public["aggregate_type"], "mission")
        self.assertNotIn("eventId", public)

    def test_event_payload_is_deeply_immutable_json(self) -> None:
        event = build_event(payload={"nested": {"enabled": True}})

        with self.assertRaises(TypeError):
            event.payload["added"] = True
        with self.assertRaises(TypeError):
            event.payload["nested"]["enabled"] = False
        with self.assertRaises(ValidationError):
            build_event(payload={"bad": object()})

    def test_models_accept_public_camel_case_documents(self) -> None:
        document = build_mission().to_public_dict()
        restored = Mission.model_validate(document)
        self.assertEqual(restored.workspace_id, "workspace-1")
        self.assertEqual(restored.contract_id, "contract-1")
        self.assertEqual(restored.contract_version, 1)

    def test_models_are_frozen_and_reject_unknown_fields(self) -> None:
        mission = build_mission()
        with self.assertRaises(ValidationError):
            mission.status = "RUNNING"
        with self.assertRaises(ValidationError):
            build_mission(unknown_field=True)

    def test_decision_rejects_unknown_evaluation_policy_reason(self) -> None:
        with self.assertRaisesRegex(ValidationError, "reason_code"):
            build_decision(reason_code="model_requested_human_review")

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

    def test_contract_governance_has_stable_bounded_decision_timeout(self) -> None:
        default_contract = build_contract()
        custom_contract = build_contract(governance={"decisionTimeoutSeconds": 900})

        self.assertEqual(default_contract.governance.decision_timeout_seconds, 86_400)
        self.assertEqual(
            custom_contract.to_public_dict()["governance"],
            {"decisionTimeoutSeconds": 900},
        )
        with self.assertRaisesRegex(ValidationError, "greater than or equal to 1"):
            build_contract(governance={"decisionTimeoutSeconds": 0})
        with self.assertRaisesRegex(ValidationError, "less than or equal to 31536000"):
            build_contract(governance={"decisionTimeoutSeconds": 31_536_001})

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
        with self.assertRaisesRegex(ValidationError, "cannot delegate to itself"):
            build_work_unit(parent_work_unit_id="wu-1")
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
                created_at=datetime(2026, 8, 1),  # noqa: DTZ001
                updated_at=datetime(2026, 8, 1),  # noqa: DTZ001
            )
        with self.assertRaisesRegex(ValidationError, "updated_at cannot be earlier"):
            build_mission(updated_at=datetime(2026, 7, 31, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
