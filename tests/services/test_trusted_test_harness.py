"""The opt-in wrapper executes copies and returns evidence, not its own PASS."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.harness_types import HarnessError, HarnessRequest, HarnessResult
from app.services.tools.sandbox_executor import SandboxResult
from app.services.trusted_tests.harness import TrustedRepositoryHarness
from app.services.trusted_tests.policy import TrustedTestParameters, policy_configuration_digest
from app.services.trusted_tests.producer import TrustedLocalTestProducer
from app.services.trusted_tests.profile import freeze_test_profile
from app.services.trusted_tests.snapshot import freeze_snapshot


class CandidateWriter:
    def __init__(self, root, source): self.root, self.source = root, source
    async def execute(self, request):
        assert request.cwd == self.root
        (self.root / 'calc.py').write_text(self.source)
        return HarnessResult(sandbox=SandboxResult(True, 'candidate prepared', '', 0, 1, 'test'))


class TrustedHarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo, self.storage, self.suite = (self.root / part for part in ('repo', 'storage', 'suite'))
        for directory in (self.repo, self.storage, self.suite): directory.mkdir()
        (self.repo / 'calc.py').write_text('def add(a,b): return a-b\n')
        (self.suite / 'test_acceptance.py').write_text('from calc import add\ndef test_add(): assert add(2,1)==3\n')
        self.snapshot = freeze_snapshot(self.repo, self.storage)
        self.addCleanup(self.snapshot.close)
        self.profile = freeze_test_profile(self.suite, (sys.executable, '-m', 'pytest', '-q'), self.storage, execution_mode='trusted-local')
        self.addCleanup(self.profile.close)
        self.producer = TrustedLocalTestProducer(producer_id='producer', key_id='key', private_key=Ed25519PrivateKey.generate(), scratch_root=self.storage)
        self.policy = TrustedTestParameters.model_validate({
            'minimumTestResults': 1, 'minimumPassRate': 1.0,
            'expectedInputSnapshotDigest': self.snapshot.digest, 'testProfileDigest': self.profile.digest,
            'producerId': 'producer', 'keyId': 'key', 'executionMode': 'trusted-local',
        })

    def harness(self, source='def add(a,b): return a+b\n', **changes):
        configuration = {'evaluator': 'trusted-test-run.v1', 'workUnitKinds': ['desktop.task'], **self.policy.model_dump(mode='json')}
        arguments = dict(snapshot=self.snapshot, profile=self.profile, producer=self.producer,
                         scratch_root=self.storage, harness_factory=lambda root: CandidateWriter(root, source),
                         policy=self.policy, contract_id='contract', contract_version=1,
                         configuration_digest=policy_configuration_digest(configuration))
        return TrustedRepositoryHarness(**{**arguments, **changes})

    def request(self, timeout=15):
        return HarnessRequest('fix add', 'python', timeout, cwd=self.repo,
                              execution=HarnessExecutionContext('mission', 'unit', 1))

    async def test_native_changes_stay_in_candidate_and_exact_five_artifacts_bind_contract(self):
        before = set(self.storage.iterdir())
        result = await self.harness().execute(self.request())
        self.assertTrue(result.sandbox.success)
        self.assertEqual(len(result.artifacts), 5)
        self.assertEqual(json.loads(result.artifacts[1].content)['conclusion'], 'passed')
        self.assertEqual(json.loads(result.artifacts[-1].content)['contractId'], 'contract')
        self.assertEqual((self.repo / 'calc.py').read_text(), 'def add(a,b): return a-b\n')
        self.assertEqual(set(self.storage.iterdir()), before)

    async def test_test_failure_is_registered_as_failed_report_not_hidden(self):
        result = await self.harness('def add(a,b): return a-b\n').execute(self.request())
        self.assertEqual(json.loads(result.artifacts[1].content)['conclusion'], 'failed')
        self.assertIn('tests: failed', result.sandbox.stdout)
        self.assertEqual(len(result.artifacts), 5)

    async def test_deadline_and_missing_execution_refuse_completion(self):
        class Waiting:
            async def execute(self, _request): await asyncio.sleep(10)
        before = set(self.storage.iterdir())
        with self.assertRaises(TimeoutError):
            await self.harness(harness_factory=lambda _: Waiting()).execute(self.request(0.05))
        self.assertEqual(set(self.storage.iterdir()), before)
        with self.assertRaises(HarnessError):
            await self.harness().execute(HarnessRequest('fix', 'python', 1))

    def test_wrong_contract_configuration_digest_is_rejected_before_native_execution(self):
        with self.assertRaises(HarnessError): self.harness(configuration_digest='sha256:'+'f'*64)
