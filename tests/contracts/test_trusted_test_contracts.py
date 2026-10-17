from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from app.services.trusted_tests.models import (
    ExecutionManifest,
    TrustedTestReceipt,
    parse_document,
)
from app.services.trusted_tests.signing import TrustedProducerRegistry
from app.services.verification_policy_service import StrictVerificationPolicyResolver
from app.services.verification_report_models import VerificationReportError
from tests.domain.factories import build_contract, build_work_unit
from tests.services.trusted_test_fixtures import (
    signed_bundle,
    trust_registry,
    trusted_criterion,
)

DIRECTORY = Path(__file__).resolve().parents[2] / "platform/contracts/v1"


class TrustedTestContractTests(unittest.TestCase):
    def test_public_schemas_match_python_models_and_reject_ambiguous_wire_data(self):
        artifacts, observations, _ = signed_bundle()
        documents = {item.artifact_id: item.report_content for item in observations}
        manifest = next(artifact for artifact in artifacts if "-manifest-" in artifact.id)
        receipt = next(artifact for artifact in artifacts if "-receipt-" in artifact.id)
        fixtures = (("execution-manifest", ExecutionManifest, documents[manifest.id]),
                    ("trusted-test-receipt", TrustedTestReceipt, documents[receipt.id]),
                    ("trusted-test-producer-keys", TrustedProducerRegistry, trust_registry().model_dump_json().encode()))
        for name, model, content in fixtures:
            schema = json.loads((DIRECTORY / f"{name}.schema.json").read_text(encoding="utf-8"))
            expected = model.model_json_schema()
            self.assertEqual({key: value for key, value in schema.items() if key not in {"$schema", "$id"}}, expected)
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
            document = json.loads(content)
            validator.validate(document)
            for mutation in ({"schemaVersion": 2}, {"schemaVersion": True}, {"foreignKey": "self-authorized"}):
                with self.subTest(name=name, mutation=mutation), self.assertRaises(ValidationError):
                    validator.validate({**document, **mutation})
            self.assertEqual(parse_document(content, model).model_dump(mode="json"), document)
            missing = dict(document)
            missing.pop("schemaVersion")
            with self.assertRaises(VerificationReportError):
                parse_document(json.dumps(missing).encode(), model)

    def test_new_discovery_policy_does_not_accept_embedded_keys_or_boolean_report_counts(self):
        artifacts, _, _ = signed_bundle()
        resolver = StrictVerificationPolicyResolver()
        contract = build_contract(acceptance_criteria=[trusted_criterion()])
        decision = resolver.resolve(contract, build_work_unit(kind="desktop.task"), artifacts)
        schema = json.loads((DIRECTORY / "verification-evaluation-policy.schema.json").read_text(encoding="utf-8"))
        documents = [json.loads(path.read_text(encoding="utf-8")) for path in DIRECTORY.glob("*.schema.json")]
        registry = Registry().with_resources((document["$id"], Resource.from_contents(document)) for document in documents)
        validator = Draft202012Validator(schema, registry=registry)
        document = decision.to_public_dict()
        validator.validate(document)
        for mutation in ({"minimumTestResults": True}, {"publicKeyBase64": "injected"}, {"executionMode": "remote"}):
            with self.subTest(mutation=mutation), self.assertRaises(ValidationError):
                validator.validate({**document, "parameters": {**document["parameters"], **mutation}})
