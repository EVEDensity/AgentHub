"""Real local pytest production against frozen external acceptance suites."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.services.trusted_tests.models import ExecutionManifest, TrustedTestReceipt, parse_document
from app.services.trusted_tests.process import ProcessResult
from app.services.trusted_tests.producer import ProductionContext, TrustedLocalTestProducer
from app.services.trusted_tests.profile import freeze_test_profile, validate_profile
from app.services.trusted_tests.signing import TrustedProducerRegistry, verify_receipt
from app.services.trusted_tests.snapshot import SnapshotError, capture_candidate, freeze_snapshot, materialize_candidate


class TamperedXMLBackend:
    async def run(self, argv, **_kwargs):
        path = Path(next(item.split('=', 1)[1] for item in argv if item.startswith('--junitxml=')))
        path.write_text('<testsuite tests="99" failures="0" errors="0" skipped="0"><testcase name="fake"/></testsuite>')
        return ProcessResult(0, b'fake XML', False, False, False, 0.01)


class TrustedProducerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo, self.storage, self.suite = (self.root / name for name in ('repo', 'storage', 'suite'))
        for directory in (self.repo, self.storage, self.suite): directory.mkdir()
        (self.repo / 'calc.py').write_text('def add(a,b): return a-b\n')
        (self.repo / '.env').write_text('MODEL_KEY=must-not-copy')
        (self.repo / 'tests').mkdir()
        (self.repo / 'tests' / 'test_owned.py').write_text('def test_original(): assert False\n')
        self.snapshot = freeze_snapshot(self.repo, self.storage)
        self.addCleanup(self.snapshot.close)
        self.candidate = materialize_candidate(self.snapshot, self.root / 'candidate')
        self.key = Ed25519PrivateKey.generate()

    def profile(self, source, **options):
        (self.suite / 'test_acceptance.py').write_text(source)
        profile = freeze_test_profile(self.suite, (sys.executable, '-m', 'pytest', '-q'), self.storage,
                                      execution_mode='trusted-local', **options)
        self.addCleanup(profile.close)
        return profile

    def context(self, profile):
        return ProductionContext('mission', 'unit', 1, self.snapshot, capture_candidate(self.snapshot, self.candidate),
                                 profile, 'contract', 1, 'sha256:'+'a'*64)

    def producer(self, **options):
        return TrustedLocalTestProducer(producer_id='producer', key_id='key', private_key=self.key,
                                        scratch_root=self.storage, **options)

    async def test_real_patch_external_pytest_signature_and_manifest(self):
        profile = self.profile('from calc import add\ndef test_add(): assert add(2,1)==3\n')
        (self.candidate / 'calc.py').write_text('def add(a,b): return a+b\n')
        bundle = await self.producer().produce(self.context(profile))
        self.assertEqual(bundle.conclusion, 'passed')
        self.assertEqual([artifact.kind for artifact in bundle.artifacts], ['diff', 'test-result', 'log', 'report', 'report'])
        self.assertIn(b'diff --git', bundle.artifacts[0].content)
        report = json.loads(bundle.artifacts[1].content)
        self.assertEqual((report['total'], report['passed'], report['exitCode']), (1, 1, 0))
        receipt = parse_document(bundle.artifacts[3].content, TrustedTestReceipt)
        registry = TrustedProducerRegistry.model_validate({'schemaVersion': 1, 'keys': [{
            'producerId': 'producer', 'keyId': 'key', 'publicKeyBase64': base64.b64encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode(),
            'testProfileDigests': [profile.digest],
        }]})
        verify_receipt(receipt, registry)
        manifest = parse_document(bundle.artifacts[4].content, ExecutionManifest)
        self.assertEqual(manifest.report.digest, bundle.artifacts[1].digest)
        self.assertEqual((self.repo / 'calc.py').read_text(), 'def add(a,b): return a-b\n')

    async def test_agent_tests_config_and_conftest_cannot_replace_frozen_operator_suite(self):
        profile = self.profile('from calc import add\ndef test_add(): assert add(2,1)==3\n')
        (self.candidate / 'conftest.py').write_text('raise AssertionError("candidate conftest must not load")\n')
        (self.candidate / 'pytest.ini').write_text('[pytest]\naddopts = --ignore=all\n')
        (self.suite / 'test_acceptance.py').write_text('def test_replaced(): assert True\n')
        for delete in (False, True):
            owned = self.candidate / 'tests' / 'test_owned.py'
            if delete: owned.unlink()
            else: owned.write_text('def test_fake(): assert True\n')
            bundle = await self.producer().produce(self.context(profile))
            report = json.loads(bundle.artifacts[1].content)
            self.assertEqual(bundle.conclusion, 'failed')
            self.assertEqual((report['failed'], report['passed']), (1, 0))
            self.assertIn('test_add', report['cases'][0]['id'])

    async def test_runtime_changes_to_materialized_tests_or_candidate_refuse_pass(self):
        for body in (
            'from pathlib import Path\ndef test_edit(): Path(__file__).write_text("replaced")\n',
            'from pathlib import Path\ndef test_edit(): Path("calc.py").write_text("answer=999")\n',
        ):
            profile = self.profile(body)
            bundle = await self.producer().produce(self.context(profile))
            self.assertEqual(bundle.conclusion, 'inconclusive')
            self.assertIn(b'"treeChanged":true', bundle.artifacts[2].content)

    async def test_zero_tests_all_skipped_and_counter_tampering_never_pass(self):
        for source in ('value = 1\n', 'import pytest\n@pytest.mark.skip(reason="skip")\ndef test_skip(): pass\n'):
            profile = self.profile(source)
            bundle = await self.producer().produce(self.context(profile))
            self.assertNotEqual(bundle.conclusion, 'passed')
        profile = self.profile('def test_ok(): assert True\n')
        bundle = await self.producer(process_backend=TamperedXMLBackend()).produce(self.context(profile))
        self.assertEqual(bundle.conclusion, 'inconclusive')
        self.assertIn(b'counters disagree', bundle.artifacts[2].content)

    async def test_child_environment_does_not_inherit_tokens_or_copy_secret_files(self):
        profile = self.profile('import os\nfrom pathlib import Path\ndef test_no_secrets():\n assert "AGENTHUB_RUNNER_TOKEN" not in os.environ\n assert "AGENTHUB_MODEL_API_KEY" not in os.environ\n assert not Path(".env").exists()\n')
        with patch.dict(os.environ, {'AGENTHUB_RUNNER_TOKEN': 'sensitive', 'AGENTHUB_MODEL_API_KEY': 'sensitive'}):
            bundle = await self.producer().produce(self.context(profile))
        self.assertEqual(bundle.conclusion, 'passed')
        self.assertNotIn(b'sensitive', bundle.artifacts[2].content)

    async def test_timeout_cannot_pass_and_cancel_cleans_owned_directories(self):
        source = 'import time\ndef test_slow(): time.sleep(10)\n'
        profile = self.profile(source, timeout_seconds=0.2)
        bundle = await self.producer().produce(self.context(profile))
        self.assertEqual(bundle.conclusion, 'inconclusive')
        self.assertTrue(json.loads(bundle.artifacts[3].content)['timedOut'])
        profile = self.profile(source, timeout_seconds=15)
        context = self.context(profile)
        before = set(self.storage.iterdir())
        cancel = asyncio.Event()
        running = asyncio.create_task(self.producer().produce(context, cancel))
        await asyncio.sleep(0.25); cancel.set()
        with self.assertRaises(asyncio.CancelledError): await running
        self.assertEqual(set(self.storage.iterdir()), before)

    def test_profile_requires_explicit_mode_and_detects_frozen_suite_tampering(self):
        profile = self.profile('def test_ok(): pass\n')
        with self.assertRaises(SnapshotError): validate_profile(replace(profile, execution_mode='automatic'))
        with self.assertRaises(SnapshotError): validate_profile(replace(profile, argv=(sys.executable, '-m', 'pytest', 'agent-test.py')))
        (profile.suite.root / 'test_acceptance.py').write_text('tampered')
        with self.assertRaises(SnapshotError): validate_profile(profile)


    def test_profile_refuses_a_different_absolute_interpreter_before_freezing(self):
        different = self.root / 'different-python.exe'
        different.write_bytes(b'not the selected interpreter')
        before = set(self.storage.iterdir())
        with self.assertRaisesRegex(SnapshotError, 'current Python interpreter'):
            freeze_test_profile(self.suite, (str(different), '-m', 'pytest'), self.storage, execution_mode='trusted-local')
        self.assertEqual(set(self.storage.iterdir()), before)

    async def test_git_operator_suite_uses_byte_tree_identity_after_execution(self):
        source = 'from calc import add\ndef test_add(): assert add(2,1)==3\n'
        (self.suite / 'test_acceptance.py').write_text(source)
        git = ['git', '-c', 'user.name=AgentHub Tests', '-c', 'user.email=tests@example.invalid',
               '-c', 'commit.gpgsign=false', '-c', f'core.hooksPath={os.devnull}']
        for arguments in (['init', '-q'], ['add', '.'], ['commit', '-qm', 'operator baseline']):
            subprocess.run([*git, *arguments], cwd=self.suite, check=True, capture_output=True)
        profile = self.profile(source)
        self.assertEqual(profile.suite.source_type, 'git-commit')
        self.assertNotEqual(profile.suite.tree_digest, profile.suite.digest)
        (self.candidate / 'calc.py').write_text('def add(a,b): return a+b\n')
        bundle = await self.producer().produce(self.context(profile))
        self.assertEqual(bundle.conclusion, 'passed')
