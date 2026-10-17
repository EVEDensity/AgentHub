"""Strict stop acknowledgements and operator-only private network probe scope."""

from __future__ import annotations

import unittest

import httpx

from app.services.opensandbox_backend import OpenSandboxBackend
from app.services.opensandbox_protocol import (
    OpenSandboxConfig,
    OpenSandboxError,
    canary_address,
)
from tests.services.test_opensandbox_backend import FIRST, JOB, KEY, WirePeer


class PrivateCanaryTests(unittest.TestCase):
    def test_only_one_canonical_rfc1918_ipv4_is_allowed(self):
        for value in (None, True, 1, "localhost", "https://10.0.0.1", "10.0.0.0/8", "10.0.0.1:80",
                      "8.8.8.8", "127.0.0.1", "169.254.169.254", "0.0.0.0", "192.0.2.1", "224.0.0.1",
                      "::1", "fd00::1", "10.00.0.1", " 10.0.0.1", "10.0.0.1\n"):
            with self.subTest(value=value), self.assertRaises(OpenSandboxError):
                canary_address(value)
        for value in ("10.0.0.1", "172.17.0.1", "192.168.1.1"):
            self.assertEqual(canary_address(value), value)


class StopAndNetworkControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.peer = WirePeer()
        self.backend = OpenSandboxBackend(OpenSandboxConfig("http://localhost", KEY, enabled=True),
                                          transport=httpx.MockTransport(self.peer))
        self.addAsyncCleanup(self.backend.close)

    async def test_network_control_is_disabled_before_any_io_by_default(self):
        with self.assertRaisesRegex(OpenSandboxError, "disabled"):
            await self.backend.create_network_control(JOB)
        self.assertEqual(self.peer.requests, [])

    async def test_network_control_has_one_operator_ip_rule_without_changing_default_create(self):
        backend = OpenSandboxBackend(OpenSandboxConfig("http://localhost", KEY, enabled=True,
            canary_control_host="172.17.0.1"), transport=httpx.MockTransport(self.peer))
        try:
            default = await backend.create(JOB)
            control = await backend.create_network_control(JOB)
            self.assertEqual(self.peer.sandboxes[default]["networkPolicy"], {"defaultAction": "deny", "egress": []})
            body = self.peer.sandboxes[control]
            self.assertEqual(body["networkPolicy"], {"defaultAction": "deny",
                "egress": [{"action": "allow", "target": "172.17.0.1"}]})
            self.assertEqual(body["metadata"], {"agenthub.job": JOB})
            self.assertNotIn("volumes", body)
        finally:
            await backend.close()

    async def _running_command(self):
        sandbox = await self.backend.create(JOB)
        self.peer.next_mode = "running"
        execution = await self.backend.start_command(sandbox, ["long-running"])
        return sandbox, execution

    async def test_already_terminal_is_confirmed_without_delete(self):
        sandbox = await self.backend.create(JOB)
        execution = await self.backend.start_command(sandbox, ["already-done"])
        stopped = await self.backend.interrupt(sandbox, execution)
        self.assertEqual(stopped.action, "confirmed_terminal")
        self.assertEqual(stopped.exit_code, 0)
        self.assertFalse(any(r.method == "DELETE" for r in self.peer.requests))
        self.assertIn(sandbox, self.peer.sandboxes)

    async def _delete_race(self, mode):
        sandbox, execution = await self._running_command()

        async def race(request):
            if request.method == "DELETE" and request.url.path.endswith("/command"):
                command = self.peer.commands[execution]
                command["running"] = mode == "running"
                if mode == "unknown":
                    command["mode"] = "unknown-exit"
                return httpx.Response(400, json={"code": "RUNTIME_ERROR", "message": KEY})
            return await self.peer(request)

        backend = OpenSandboxBackend(OpenSandboxConfig("http://localhost", KEY, enabled=True),
                                     transport=httpx.MockTransport(race))
        backend._owned.add(sandbox)
        try:
            stopped = await backend.interrupt(sandbox, execution)
            self.assertNotIn(KEY, repr(stopped))
            if mode == "terminal":
                self.assertEqual(stopped.action, "confirmed_terminal")
                self.assertEqual(stopped.exit_code, 0)
                self.assertEqual(stopped.cause["http_status"], 400)
                self.assertIn(sandbox, self.peer.sandboxes)
            else:
                self.assertEqual(stopped.action, "confirmed_sandbox_destroy")
                self.assertNotIn(sandbox, self.peer.sandboxes)
                self.assertEqual([(r.method, r.url.path) for r in self.peer.requests[-2:]],
                    [("DELETE", f"/v1/sandboxes/{sandbox}"), ("GET", f"/v1/sandboxes/{sandbox}")])
        finally:
            await backend.close()

    async def test_delete_failure_racing_with_terminal_is_safe_and_idempotent(self):
        await self._delete_race("terminal")

    async def test_delete_failure_with_running_command_destroys_and_confirms_absence(self):
        await self._delete_race("running")

    async def test_delete_failure_with_unknown_exit_destroys_and_confirms_absence(self):
        await self._delete_race("unknown")

    async def test_timeout_records_a_terminal_ack_and_actual_exit(self):
        sandbox = await self.backend.create(JOB)
        self.peer.next_mode = "running"
        with self.assertRaises(OpenSandboxError) as failure:
            await self.backend.execute(sandbox, ["long-running"], timeout_seconds=0.01)
        diagnostic = failure.exception.diagnostic()
        self.assertEqual(diagnostic["cleanup_action"], "confirmed_interrupt")
        self.assertEqual(diagnostic["exit_code"], 137)
        self.assertIn(sandbox, self.peer.sandboxes)

    async def test_unknown_sandbox_cannot_be_claimed_destroyed(self):
        with self.assertRaises(OpenSandboxError) as failure:
            await self.backend.interrupt(FIRST, "cmd-unknown")
        self.assertEqual(failure.exception.diagnostic()["code"], "sandbox_not_owned")
        self.assertEqual(self.peer.requests, [])
