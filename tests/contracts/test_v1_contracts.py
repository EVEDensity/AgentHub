from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.services.mission_service import (
    VerificationContext,
    VerificationDiscoveryOutcome,
)
from app.services.verification_policy_service import StrictVerificationPolicyResolver
from tests.domain.factories import (
    build_artifact,
    build_contract,
    build_mission,
    build_work_unit,
)

CONTRACT_DIR = Path(__file__).parents[2] / "platform" / "contracts" / "v1"


class PublicContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.documents = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in CONTRACT_DIR.glob("*.json")
        }
        resources = [
            (document["$id"], Resource.from_contents(document))
            for document in cls.documents.values()
            if "$id" in document
        ]
        cls.registry = Registry().with_resources(resources)

    def test_all_schemas_are_valid_draft_2020_12(self) -> None:
        schema_names = {
            name for name in self.documents if name.endswith(".schema.json")
        }
        self.assertEqual(
            schema_names,
            {
                "common.schema.json",
                "mission.schema.json",
                "mission-contract.schema.json",
                "work-unit.schema.json",
                "work-unit-claim-response.schema.json",
                "verification-work-discovery-response.schema.json",
                "verification-evaluation-policy.schema.json",
                "decision.schema.json",
                "artifact.schema.json",
                "evidence.schema.json",
                "event-envelope.schema.json",
                "execution-checkpoint.schema.json",
            },
        )
        for name in schema_names:
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(self.documents[name])

    def test_schema_identifiers_are_unique_and_versioned(self) -> None:
        identifiers = [
            document["$id"] for document in self.documents.values() if "$id" in document
        ]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(all("/v1/" in identifier for identifier in identifiers))

    def test_representative_documents_validate(self) -> None:
        digest = "sha256:" + "a" * 64
        actor = {"type": "human", "id": "user-1"}
        timestamp = "2026-08-01T00:00:00Z"
        examples = {
            "mission.schema.json": {
                "id": "mis-1",
                "workspaceId": "workspace-1",
                "title": "Fix issue 42",
                "objective": "Produce a tested, reviewable pull request.",
                "source": {
                    "type": "issue",
                    "reference": "https://example.test/issues/42",
                },
                "contractId": "contract-1",
                "contractVersion": 1,
                "status": "READY",
                "planVersion": 0,
                "createdBy": actor,
                "createdAt": timestamp,
                "updatedAt": timestamp,
            },
            "mission-contract.schema.json": {
                "id": "contract-1",
                "version": 1,
                "repositoryScopes": [
                    {"repository": "owner/repo", "baseRef": "main", "paths": ["app/**"]}
                ],
                "allowedCapabilities": [{"capability": "repository.write"}],
                "budgets": {"timeSeconds": 3600, "modelCost": 10, "retries": 2},
                "acceptanceCriteria": [
                    {
                        "id": "tests",
                        "kind": "test",
                        "description": "Tests pass",
                        "required": True,
                    }
                ],
                "decisionGates": [],
                "forbiddenActions": ["repository.force_push"],
            },
            "work-unit.schema.json": {
                "id": "wu-1",
                "missionId": "mis-1",
                "parentWorkUnitId": "wu-parent",
                "assignedAgentId": "reviewer",
                "kind": "code_change",
                "dependencies": [],
                "inputRefs": [],
                "expectedOutputs": [{"kind": "diff", "required": True}],
                "requiredCapabilities": ["repository.write"],
                "status": "PENDING",
                "attempt": 0,
            },
            "work-unit-claim-response.schema.json": {
                "claimStatus": "idle",
                "workUnit": None,
            },
            "verification-work-discovery-response.schema.json": {
                "discoveryStatus": "ready",
                "verificationContext": {
                    "version": 3,
                    "mission": {
                        "id": "mis-1",
                        "title": "Fix issue 42",
                        "objective": "Produce a tested, reviewable pull request.",
                    },
                    "contract": {
                        "id": "contract-1",
                        "version": 1,
                        "acceptanceCriteria": [
                            {
                                "id": "tests",
                                "kind": "test",
                                "description": "Tests pass",
                                "required": True,
                                "configuration": {},
                            }
                        ],
                    },
                    "workUnit": {
                        "id": "wu-1",
                        "kind": "code_change",
                        "inputRefs": [],
                        "expectedOutputs": [{"kind": "diff", "required": True}],
                        "status": "VERIFYING",
                        "attempt": 1,
                    },
                    "artifacts": [
                        {
                            "id": "artifact-1",
                            "attempt": 1,
                            "kind": "diff",
                            "digest": digest,
                            "contentAddress": "local:sha256/" + "a" * 64,
                            "mediaType": "text/x-diff",
                            "sizeBytes": 128,
                            "sensitivity": "internal",
                        }
                    ],
                    "evaluationPolicy": {
                        "status": "inconclusive",
                        "reasonCode": "no_applicable_policy",
                        "criterionIds": ["tests"],
                    },
                },
            },
            "verification-evaluation-policy.schema.json": {
                "status": "ready",
                "criterionId": "tests",
                "evaluator": "artifact-set.v1",
                "configurationDigest": digest,
                "parameters": {
                    "minimumArtifacts": 1,
                    "requiredArtifactKinds": ["test-result"],
                },
            },
            "artifact.schema.json": {
                "id": "artifact-1",
                "missionId": "mis-1",
                "workUnitId": "wu-1",
                "attempt": 1,
                "kind": "diff",
                "digest": digest,
                "contentAddress": "local:sha256/" + "a" * 64,
                "mediaType": "text/x-diff",
                "sizeBytes": 128,
                "retention": "mission",
                "sensitivity": "internal",
                "createdBy": actor,
                "createdAt": timestamp,
            },
            "evidence.schema.json": {
                "id": "evd-1",
                "missionId": "mis-1",
                "criterionId": "tests",
                "verifier": {"id": "pytest", "version": "9.0"},
                "verdict": "PASS",
                "artifactRefs": [{"id": "artifact-1", "digest": digest}],
                "summary": "All required tests passed.",
                "generatedAt": timestamp,
                "integrityHash": digest,
            },
            "decision.schema.json": {
                "id": "dec-1",
                "missionId": "mis-1",
                "workUnitId": "wu-1",
                "attempt": 1,
                "contextDigest": digest,
                "reasonCode": "no_applicable_policy",
                "criterionIds": ["tests"],
                "options": ["RETRY_WORK_UNIT", "FAIL_MISSION"],
                "recommendedOption": "FAIL_MISSION",
                "riskSummary": "Verification policy cannot prove the criteria.",
                "status": "PENDING",
                "version": 1,
                "requestedBy": {"type": "service", "id": "mission-control"},
                "requestedAt": timestamp,
            },
            "event-envelope.schema.json": {
                "event_id": "evt-1",
                "aggregate_type": "work_unit",
                "aggregate_id": "wu-1",
                "sequence": 1,
                "event_type": "work_unit.execution.started",
                "actor": {"type": "runner", "id": "runner-1"},
                "occurred_at": timestamp,
                "correlation_id": "mis-1",
                "payload": {"attempt": 1},
                "schema_version": 1,
            },
        }
        for schema_name, instance in examples.items():
            with self.subTest(schema=schema_name):
                Draft202012Validator(
                    self.documents[schema_name], registry=self.registry
                ).validate(instance)

    def test_work_unit_claim_status_matches_payload(self) -> None:
        validator = Draft202012Validator(
            self.documents["work-unit-claim-response.schema.json"],
            registry=self.registry,
        )
        claimed_work_unit = {
            "id": "wu-1",
            "missionId": "mis-1",
            "kind": "code_change",
            "dependencies": [],
            "inputRefs": [],
            "expectedOutputs": [],
            "requiredCapabilities": [],
            "status": "LEASED",
            "attempt": 1,
            "lease": {
                "id": "lease-1",
                "runnerId": "runner-1",
                "expiresAt": "2026-08-15T01:00:00Z",
            },
        }

        validator.validate({"claimStatus": "claimed", "workUnit": claimed_work_unit})
        validator.validate({"claimStatus": "capacity_saturated", "workUnit": None})
        invalid_documents = [
            {"claimStatus": "claimed", "workUnit": None},
            {"claimStatus": "idle", "workUnit": claimed_work_unit},
            {"claimStatus": "unknown", "workUnit": None},
        ]
        for document in invalid_documents:
            with self.subTest(document=document):
                self.assertTrue(list(validator.iter_errors(document)))

    def test_verification_discovery_status_matches_payload(self) -> None:
        validator = Draft202012Validator(
            self.documents["verification-work-discovery-response.schema.json"],
            registry=self.registry,
        )
        validator.validate({"discoveryStatus": "idle", "verificationContext": None})
        for document in (
            {"discoveryStatus": "ready", "verificationContext": None},
            {"discoveryStatus": "idle", "verificationContext": {}},
            {"discoveryStatus": "unknown", "verificationContext": None},
        ):
            with self.subTest(document=document):
                self.assertTrue(list(validator.iter_errors(document)))

    def test_evaluation_policy_status_matches_payload(self) -> None:
        validator = Draft202012Validator(
            self.documents["verification-evaluation-policy.schema.json"],
            registry=self.registry,
        )
        validator.validate(
            {
                "status": "inconclusive",
                "reasonCode": "no_applicable_policy",
                "criterionIds": ["tests"],
            }
        )
        validator.validate(
            {
                "status": "inconclusive",
                "reasonCode": "no_applicable_policy",
                "criterionIds": [],
            }
        )
        invalid_documents = (
            {"status": "ready", "reasonCode": "no_applicable_policy"},
            {
                "status": "ready",
                "criterionId": "tests",
                "evaluator": "artifact-set.v1",
                "configurationDigest": "sha256:" + "a" * 64,
                "parameters": {
                    "minimumArtifacts": 1,
                    "requiredArtifactKinds": [],
                },
                "criterionIds": [],
            },
            {"status": "inconclusive", "criterionId": "tests"},
            {
                "status": "inconclusive",
                "reasonCode": "unknown",
                "criterionIds": [],
            },
            {
                "status": "inconclusive",
                "reasonCode": "no_applicable_policy",
            },
            {
                "status": "inconclusive",
                "reasonCode": "ambiguous_policy",
                "criterionIds": ["tests", "tests"],
            },
        )
        for document in invalid_documents:
            with self.subTest(document=document):
                self.assertTrue(list(validator.iter_errors(document)))

    def test_verification_discovery_producer_matches_contract(self) -> None:
        validator = Draft202012Validator(
            self.documents["verification-work-discovery-response.schema.json"],
            registry=self.registry,
        )
        context = VerificationContext(
            mission=build_mission(status="RUNNING"),
            contract=build_contract(),
            work_unit=build_work_unit(status="VERIFYING", attempt=1),
            artifacts=(build_artifact(),),
            evaluation_policy=StrictVerificationPolicyResolver().resolve(
                build_contract(),
                build_work_unit(status="VERIFYING", attempt=1),
                (build_artifact(),),
            ),
        )

        validator.validate(
            VerificationDiscoveryOutcome(context=context).to_public_dict()
        )
        validator.validate(VerificationDiscoveryOutcome(context=None).to_public_dict())

    def test_event_catalog_is_unique_and_matches_envelope_aggregates(self) -> None:
        catalog = self.documents["event-catalog.json"]
        self.assertEqual(catalog["catalogVersion"], 1)
        event_types = [event["eventType"] for event in catalog["events"]]
        self.assertEqual(len(event_types), len(set(event_types)))

        allowed_aggregates = set(
            self.documents["event-envelope.schema.json"]["properties"][
                "aggregate_type"
            ]["enum"]
        )
        for event in catalog["events"]:
            with self.subTest(event=event["eventType"]):
                self.assertIn(event["aggregateType"], allowed_aggregates)
                self.assertRegex(
                    event["eventType"], r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}$"
                )
                self.assertEqual(
                    len(event["payloadRequired"]), len(set(event["payloadRequired"]))
                )


if __name__ == "__main__":
    unittest.main()
