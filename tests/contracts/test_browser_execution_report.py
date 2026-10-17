"""Actual diagnostic producer over a deterministic browser port, not Chromium."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from app.services.browser_read.backend import BrowserReadResult
from app.services.browser_read.harness import BrowserNativeHarness
from app.services.harness_service import FunctionCallingHarness
from app.services.harness_types import HarnessExecutionContext, HarnessRequest
from tests.services.test_browser_read_backend import wire_result
from tests.services.test_browser_read_harness import FixtureBackend, FixtureModel
from tests.services.test_browser_read_profile import profile_fixture


class BrowserObservationContractTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parents[2] / "platform/contracts/v1/browser-execution-report.schema.json"
        cls.schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema, format_checker=FormatChecker())

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = profile_fixture(Path(temporary.name).resolve())
        self.execution = HarnessExecutionContext("mis-1", "wu-1", 2)

    async def produce(self, *, calls=2, results=()):
        backend = FixtureBackend(self.profile, results)
        model = FixtureModel(calls=calls)
        harness = BrowserNativeHarness(
            execution=self.execution, backend=backend, contract_id="contract-1", contract_version=3,
            native_factory=lambda tools: FunctionCallingHarness(model, list(tools), model_retries=0),
        )
        result = await harness.execute(HarnessRequest("Read approved documents", "text", 5, execution=self.execution))
        self.assertEqual([artifact.kind for artifact in result.artifacts], ["test-result", "report"])
        document = json.loads(result.artifacts[-1].content)
        self.validator.validate(document)
        return result, document

    async def test_actual_producer_binds_scope_runtime_and_ordered_read_observations(self):
        result, document = await self.produce()
        self.assertTrue(result.sandbox.success)
        self.assertEqual((document["missionId"], document["workUnitId"], document["attempt"]), ("mis-1", "wu-1", 2))
        self.assertEqual((document["contractId"], document["contractVersion"]), ("contract-1", 3))
        self.assertEqual(document["profileDigest"], self.profile.digest)
        self.assertEqual(document["runtimeSha256"], self.profile.runtime_sha256)
        self.assertEqual(document["origins"], list(self.profile.origins))
        self.assertEqual([read["sequence"] for read in document["reads"]], [1, 2])
        self.assertTrue(document["cleanupConfirmed"])
        for read in document["reads"]:
            self.assertLessEqual(read["startedAt"], read["finishedAt"])
            self.assertEqual(read["result"]["text"], "Observed text")

    async def test_failed_empty_and_unconfirmed_cleanup_reports_are_still_only_diagnostics(self):
        cases = (
            {"calls": 0},
            {"calls": 1, "results": [wire_result(success=False, errorCode="http_error", httpStatus=404)]},
            {"calls": 1, "results": [wire_result(cleanupConfirmed=False)]},
        )
        for options in cases:
            with self.subTest(options=options):
                result, document = await self.produce(**options)
                self.assertFalse(result.sandbox.success)
                self.assertEqual(document["acceptance"], "artifact-integrity-only")
                self.assertNotIn("verdict", document)
                self.assertNotIn("conclusion", document)

    def test_nested_result_schema_is_frozen_to_the_strict_public_worker_dto(self):
        self.assertEqual(self.schema["$defs"]["browserReadResult"], BrowserReadResult.model_json_schema(by_alias=True))

    async def test_unknown_fields_fake_test_claims_and_wrong_scope_or_runtime_shapes_are_rejected(self):
        _, document = await self.produce()
        mutations = (
            {"schema": "agenthub.browser-execution-report.v2"}, {"missionId": ""}, {"attempt": True},
            {"contractId": None}, {"contractVersion": False}, {"profile": "browser-admin"},
            {"profileDigest": "sha256:" + "x" * 64}, {"runtimeSha256": "abc"},
            {"playwrightVersion": "latest"}, {"upstreamRevision": "main"},
            {"origins": "https://example.com"}, {"origins": []},
            {"origins": ["https://example.com", "https://example.com"]},
            {"origins": ["https://example.com/private"]}, {"reads": document["reads"] * 17},
            {"cleanupConfirmed": "true"}, {"modelOutputTruncated": 0}, {"timestampSource": "browser-clock"},
            {"acceptance": "tests-passed"}, {"verdict": "PASS"}, {"passedTests": 4},
            {"conclusion": "passed", "cases": [], "summary": {"passed": 4}}, {"stdout": "invented"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValidationError):
                self.validator.validate(document | mutation)

    async def test_read_and_worker_unknown_fields_types_and_digest_shapes_are_rejected(self):
        _, document = await self.produce()
        read_changes = (
            {"sequence": False}, {"sequence": 0}, {"startedAt": "yesterday"}, {"finishedAt": None},
            {"status": "passed"}, {"errorCode": "unbounded-exception"}, {"stdout": "invented"},
        )
        result_changes = (
            {"schema": "agenthub.browser-read.v2"}, {"success": "true"}, {"httpStatus": True},
            {"textSha256": "sha256:" + "A" * 64}, {"textBytes": -1}, {"truncated": "false"},
            {"blockedResourceCount": False}, {"workerPid": True}, {"cleanupConfirmed": 1},
            {"playwrightVersion": "1.0.0"}, {"passedTests": 1},
        )
        for location, mutations in (("read", read_changes), ("result", result_changes)):
            for mutation in mutations:
                changed = copy.deepcopy(document)
                target = changed["reads"][0]
                if location == "result":
                    target = target["result"]
                target.update(mutation)
                with self.subTest(location=location, mutation=mutation), self.assertRaises(ValidationError):
                    self.validator.validate(changed)
