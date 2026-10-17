"""Diagnostic observations cannot claim trusted tests or separate output streams."""
import copy
import json
import unittest
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from app.services.harness_service import FunctionCallingHarness, HarnessExecutionContext, HarnessRequest
from app.services.opensandbox_backend import OpenSandboxBackend
from app.services.opensandbox_harness import SandboxNativeHarness
from app.services.opensandbox_protocol import OpenSandboxConfig
from tests.services.test_opensandbox_backend import JOB, KEY
from tests.services.test_opensandbox_harness import FixtureModel, NativeWirePeer


class OpenSandboxObservationContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_producer_report_matches_public_diagnostic_format(self):
        schema = json.loads((Path(__file__).parents[2] / 'platform/contracts/v1/opensandbox-execution-report.schema.json')
                            .read_text(encoding='utf-8'))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        backend = OpenSandboxBackend(OpenSandboxConfig('http://localhost', KEY, enabled=True),
                                    transport=httpx.MockTransport(NativeWirePeer()))
        self.addAsyncCleanup(backend.close)
        execution = HarnessExecutionContext('mission', 'unit', 1)
        model = FixtureModel(['printf command'])
        harness = SandboxNativeHarness(execution=execution, backend=backend, job_id=JOB,
            native_factory=lambda tools: FunctionCallingHarness(model, list(tools), model_retries=0))
        result = await harness.execute(HarnessRequest('run the scoped command', 'text', 5, execution=execution))
        document = json.loads(result.artifacts[-1].content)
        validator.validate(document)
        mutations = ({'schema': 'agenthub.opensandbox-execution-report.v2'}, {'attempt': True},
                     {'cleanupConfirmed': False}, {'image': 'ubuntu:latest'},
                     {'acceptance': 'tests-passed'}, {'stdout': 'fabricated split output'})
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValidationError):
                validator.validate({**document, **mutation})
        for mutation in ({'exitCode': '0'}, {'outputStream': 'stdout'}, {'script': 'raw source'}, {'sequence': False}):
            changed = copy.deepcopy(document)
            changed['commands'][0].update(mutation)
            with self.subTest(command=mutation), self.assertRaises(ValidationError):
                validator.validate(changed)
