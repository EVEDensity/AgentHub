"""Real budget/Runner/Verifier flow; OpenSandbox wire is mocked in this suite.

The disposable Linux conformance job runs the same scenario with real Docker.
"""
import unittest

import httpx

from app.services.opensandbox_backend import OpenSandboxBackend
from app.services.opensandbox_protocol import OpenSandboxConfig
from tests.integration.opensandbox_native_scenario import MARKER, exercise_budgeted_roundtrip
from tests.services.test_opensandbox_backend import JOB, KEY
from tests.services.test_opensandbox_harness import NativeWirePeer


class MarkerWirePeer(NativeWirePeer):
    def command_detail(self, path):
        if path[-1] == 'logs':
            return httpx.Response(200, text=MARKER)
        return super().command_detail(path)


class OpenSandboxNativeSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def test_budgeted_native_command_observations_require_independent_integrity_verification(self):
        peer = MarkerWirePeer()
        backend = OpenSandboxBackend(OpenSandboxConfig('http://localhost', KEY, enabled=True),
                                    transport=httpx.MockTransport(peer))
        self.addAsyncCleanup(backend.close)
        result = await exercise_budgeted_roundtrip(backend, JOB)
        self.assertEqual(result['terminal_status'], 'SUCCEEDED')
        self.assertEqual(result['evidence_verdict'], 'PASS')
        self.assertEqual(result['artifacts'], 2)
        self.assertEqual(result['model_http_requests'], 2)
        self.assertEqual(result['spent_nano_usd'], 22_000)
        self.assertEqual(result['sandbox_ids'], peer.created_ids)
        self.assertEqual(peer.sandboxes, {})
