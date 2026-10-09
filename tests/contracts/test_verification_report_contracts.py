import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from app.services.verification_report_models import TestRunReport as RunReport, SecurityScanReport
from tests.services.verification_report_fixtures import make_test_report, security_report


DIRECTORY = Path(__file__).parents[2] / "platform" / "contracts" / "v1"


class VerificationReportContractTests(unittest.TestCase):
    def test_report_models_match_versioned_public_schemas(self):
        for name, model, document in (("test-run-report", RunReport, make_test_report()),
                                      ("security-scan-report", SecurityScanReport, security_report((2,)))):
            schema = json.loads((DIRECTORY / f"{name}.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(document)
            self.assertEqual(model.model_validate(document).model_dump(), document)
            for updates in ({"schemaVersion": 2}, {"schemaVersion": True}, {"attempt": True}, {"unknown": 1}):
                with self.subTest(report=name, updates=updates), self.assertRaises(ValidationError):
                    Draft202012Validator(schema).validate({**document, **updates})

    def test_discovery_parameters_admit_current_evaluators_without_mixing_fields(self):
        documents = [json.loads(path.read_text(encoding="utf-8")) for path in DIRECTORY.glob("*.schema.json")]
        registry = Registry().with_resources((document["$id"], Resource.from_contents(document)) for document in documents)
        schema = json.loads((DIRECTORY / "verification-evaluation-policy.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, registry=registry)
        cases = {
            "artifact-set.v1": {"minimumArtifacts": 1, "requiredArtifactKinds": ["diff"]},
            "artifact-set.v2": {"minimumArtifacts": 1, "requiredArtifactKinds": ["diff"], "optionalArtifactKinds": [], "maxArtifactSizeBytes": 0},
            "test-run.v1": {"minimumTestResults": 1, "minimumPassRate": 1},
            "build-artifact.v1": {"minimumBuildArtifacts": 1, "maxArtifactSizeBytes": 1024},
            "security-scan.v1": {"minimumScanReports": 1, "maxSeverity": 3},
        }
        for evaluator, parameters in cases.items():
            document = {"status": "ready", "criterionId": "reports", "evaluator": evaluator,
                        "configurationDigest": "sha256:" + "a" * 64, "parameters": parameters}
            validator.validate(document)
            with self.subTest(evaluator=evaluator), self.assertRaises(ValidationError):
                validator.validate({**document, "parameters": {**parameters, "foreignParameter": 42}})
