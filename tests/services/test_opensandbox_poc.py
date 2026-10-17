"""PoC handoff/failure reporting tests, without any external execution."""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.opensandbox_protocol import OpenSandboxError
from scripts import opensandbox_poc


class PocCliTests(unittest.TestCase):
    def test_cli_disabled_does_not_call_backend(self):
        arguments = ["--endpoint", "http://localhost:18080", "--job-id",
                     "e9e3c2d0-b785-4dd0-a7e0-71cd61fdde41", "--report", "unused.json"]
        with patch.object(opensandbox_poc, "run_poc") as execute, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(opensandbox_poc.main(arguments), 1)
        execute.assert_not_called()

    def test_refuses_existing_report_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "existing.json"
            report.write_text("preserve", encoding="utf-8")
            arguments = ["--enable", "--endpoint", "http://localhost:18080", "--job-id",
                         "e9e3c2d0-b785-4dd0-a7e0-71cd61fdde41", "--report", str(report)]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(opensandbox_poc.main(arguments), 1)
            self.assertEqual(report.read_text(encoding="utf-8"), "preserve")


class PocReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.args = argparse.Namespace(enable=True, endpoint="http://localhost:18080", job_id="unit-job")
        self.backend = SimpleNamespace(create=AsyncMock(side_effect=["first", "second"]),
            execute=AsyncMock(side_effect=[SimpleNamespace(exit_code=0, output="stdout-proof stderr-proof"),
                                          SimpleNamespace(exit_code=7, output="")]), close=AsyncMock())

    async def _run(self, **overrides):
        dependencies = {"_auth_checks": AsyncMock(), "_transfer_checks": AsyncMock(),
                        "_cancellation_check": AsyncMock(), "_timeout_check": AsyncMock(),
                        "_network_check": AsyncMock(), **overrides}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(opensandbox_poc, "OpenSandboxBackend", return_value=self.backend))
            for name, replacement in dependencies.items():
                stack.enter_context(patch.object(opensandbox_poc, name, replacement))
            return await opensandbox_poc.run_poc(self.args, "ephemeral-secret")

    async def test_success_requires_destructive_cleanup_confirmation(self):
        report = await self._run()
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["sandbox_ids"], ["first", "second"])
        self.assertTrue(report["checks"][-1]["passed"])
        self.backend.close.assert_awaited_once()

    async def test_backend_failure_is_sanitized_and_cleanup_still_runs(self):
        self.backend.create.side_effect = OpenSandboxError("ephemeral-secret")
        report = await self._run()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_type"], "OpenSandboxError")
        self.assertEqual(report["stage"], "create_first_sandbox")
        self.assertEqual(report["error"], {"code": "backend_failure", "operation": "unknown"})
        self.assertNotIn("ephemeral-secret", repr(report))
        self.backend.close.assert_awaited_once()

    async def test_report_retains_safe_http_diagnostic_without_remote_body(self):
        self.backend.create.side_effect = OpenSandboxError("ephemeral-secret", code="http_error",
            http_status=500, upstream_code="DOCKER::SANDBOX_START_FAILED", operation="sandbox_create")
        report = await self._run()
        self.assertEqual(report["stage"], "create_first_sandbox")
        self.assertEqual(report["error"], {"code": "http_error", "http_status": 500,
            "upstream_code": "DOCKER::SANDBOX_START_FAILED", "operation": "sandbox_create"})
        self.assertNotIn("ephemeral-secret", repr(report))

    async def test_cleanup_failure_revokes_success(self):
        self.backend.close.side_effect = OpenSandboxError("ephemeral-secret")
        report = await self._run()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["cleanup_error_type"], "OpenSandboxError")
        self.assertNotIn("ephemeral-secret", repr(report))

    async def test_canary_failure_cannot_be_reported_as_success(self):
        report = await self._run(_network_check=AsyncMock(side_effect=OpenSandboxError("blocked")))
        self.assertEqual(report["status"], "failed")
        self.backend.close.assert_awaited_once()

    async def test_network_control_host_requires_explicit_operator_flag(self):
        with (patch.dict(os.environ, {"AGENTHUB_OPENSANDBOX_CANARY_HOST": "172.17.0.1"}),
              patch.object(opensandbox_poc, "OpenSandboxConfig", wraps=opensandbox_poc.OpenSandboxConfig) as factory):
            await self._run()
        self.assertIsNone(factory.call_args.kwargs["canary_control_host"])

    async def test_network_control_flag_passes_the_configured_host(self):
        self.args.enable_network_control = True
        with (patch.dict(os.environ, {"AGENTHUB_OPENSANDBOX_CANARY_HOST": "172.17.0.1"}),
              patch.object(opensandbox_poc, "OpenSandboxConfig", wraps=opensandbox_poc.OpenSandboxConfig) as factory):
            await self._run()
        self.assertEqual(factory.call_args.kwargs["canary_control_host"], "172.17.0.1")

    async def test_network_stage_fails_without_flag_or_host_and_creates_no_control(self):
        self.backend.config = SimpleNamespace(canary_control_host=None)
        self.backend.create_network_control = AsyncMock()
        with patch.dict(os.environ, {"AGENTHUB_OPENSANDBOX_CANARY_HOST": ""}):
            report = await self._run(_network_check=opensandbox_poc._network_check)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stage"], "network_default_deny")
        self.backend.create_network_control.assert_not_awaited()
        self.backend.close.assert_awaited_once()


class PocNetworkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.host = "172.17.0.1"
        self.control_id = "network-control"
        self.report = {"job_id": "same-job", "sandbox_ids": ["denied"], "checks": []}
        self.backend = SimpleNamespace(
            config=SimpleNamespace(canary_control_host=self.host),
            create_network_control=AsyncMock(return_value=self.control_id),
            network_policy=AsyncMock(side_effect=[
                {"policy": {"defaultAction": "deny", "egress": []}},
                {"policy": {"defaultAction": "deny", "egress": [{"action": "allow", "target": self.host}]}},
            ]))

    async def _network(self, *, control_exit=0, control_connect=True, deny_exit=1, deny_connect=False):
        callback = None

        async def connect(*_args):
            writer = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())
            await callback(None, writer)
            return None, writer

        async def start_server(handler, host, port):
            nonlocal callback
            self.assertEqual((host, port), ("0.0.0.0", 0))
            callback = handler
            manager = SimpleNamespace(sockets=[SimpleNamespace(getsockname=lambda: (host, 53111))])
            context = AsyncMock()
            context.sockets = manager.sockets
            context.__aenter__.return_value = manager
            return context

        async def execute(sandbox_id, command, **kwargs):
            should_connect = control_connect if sandbox_id == self.control_id else deny_connect
            if should_connect:
                await connect()
            return SimpleNamespace(exit_code=control_exit if sandbox_id == self.control_id else deny_exit)

        self.backend.execute = AsyncMock(side_effect=execute)
        with (patch.object(opensandbox_poc.asyncio, "start_server", side_effect=start_server),
              patch.object(opensandbox_poc.asyncio, "open_connection", side_effect=connect),
              patch.object(opensandbox_poc.asyncio, "sleep", new_callable=AsyncMock)):
            await opensandbox_poc._network_check(self.backend, "denied", self.report)

    async def test_control_reaches_same_listener_before_deny_probe(self):
        await self._network()
        self.backend.create_network_control.assert_awaited_once_with("same-job")
        self.assertEqual(self.report["sandbox_ids"], ["denied", self.control_id])
        calls = self.backend.execute.await_args_list
        self.assertEqual([call.args[0] for call in calls], [self.control_id, "denied"])
        self.assertEqual(calls[0].args[1], calls[1].args[1])
        self.assertIn("/dev/tcp/172.17.0.1/53111", calls[0].args[1][-1])
        self.assertTrue(all(check["passed"] for check in self.report["checks"]))

    async def test_control_nonzero_exit_does_not_establish_reachability(self):
        with self.assertRaises(OpenSandboxError):
            await self._network(control_exit=1)
        self.assertEqual(self.report["checks"][-1], {
            "name": "allowed_sandbox_reaches_host_canary", "passed": False})
        self.assertEqual(self.backend.execute.await_count, 1)

    async def test_control_zero_exit_without_observed_connection_is_rejected(self):
        with self.assertRaises(OpenSandboxError):
            await self._network(control_connect=False)
        self.assertFalse(self.report["checks"][-1]["passed"])

    async def test_deny_probe_success_is_rejected(self):
        with self.assertRaises(OpenSandboxError):
            await self._network(deny_exit=0, deny_connect=True)
        self.assertEqual(self.report["checks"][-1], {
            "name": "default_deny_blocks_host_canary", "passed": False})

    async def test_deny_nonzero_after_connecting_is_rejected(self):
        with self.assertRaises(OpenSandboxError):
            await self._network(deny_exit=1, deny_connect=True)
        self.assertFalse(self.report["checks"][-1]["passed"])

    async def test_control_policy_must_allow_exactly_one_configured_ip(self):
        self.backend.network_policy.side_effect = [
            {"policy": {"defaultAction": "deny", "egress": []}},
            {"policy": {"defaultAction": "allow", "egress": []}},
        ]
        with self.assertRaises(OpenSandboxError):
            await self._network()
        self.assertEqual(self.report["sandbox_ids"], ["denied", self.control_id])
        self.backend.execute.assert_not_awaited()

    async def test_original_deny_policy_cannot_have_an_allow_rule(self):
        self.backend.network_policy.side_effect = [
            {"policy": {"defaultAction": "deny", "egress": [{"action": "allow", "target": self.host}]}}]
        with self.assertRaises(OpenSandboxError):
            await self._network()
        self.backend.create_network_control.assert_not_awaited()

    async def test_network_control_requires_opt_in_before_creating_extra_sandbox(self):
        self.backend.config.canary_control_host = None
        with self.assertRaises(OpenSandboxError):
            await self._network()
        self.backend.create_network_control.assert_not_awaited()
        self.backend.network_policy.assert_not_awaited()


class PocTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_still_requires_later_marker_check_with_timing_margin(self):
        timeout = OpenSandboxError("command timed out", code="request_timeout", operation="command_status")
        timeout.cleanup_action, timeout.exit_code = "confirmed_interrupt", 137
        backend = SimpleNamespace(execute=AsyncMock(side_effect=[timeout,
            SimpleNamespace(exit_code=0)]))
        report = {"checks": []}
        with patch.object(opensandbox_poc.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await opensandbox_poc._timeout_check(backend, "sandbox", report)
        first = backend.execute.await_args_list[0]
        self.assertEqual(first.kwargs["timeout_seconds"], 2)
        self.assertIn("sleep 5", first.args[1][-1])
        sleep.assert_awaited_once_with(6)
        self.assertEqual(len(backend.execute.await_args_list), 2)
        self.assertTrue(report["checks"][-1]["passed"])
        self.assertEqual(report["checks"][-1]["name"], "timeout_stopped_child_marker")
        self.assertEqual(report["timeout_observation"]["cleanup_action"], "confirmed_interrupt")
        self.assertEqual(report["timeout_observation"]["exit_code"], 137)

    async def test_destroyed_sandbox_does_not_count_as_a_missing_marker_success(self):
        backend = SimpleNamespace(execute=AsyncMock(side_effect=[OpenSandboxError("command timed out"),
            OpenSandboxError("not owned", code="sandbox_not_owned")]))
        report = {"checks": []}
        with (patch.object(opensandbox_poc.asyncio, "sleep", new_callable=AsyncMock),
              self.assertRaises(OpenSandboxError) as failure):
            await opensandbox_poc._timeout_check(backend, "sandbox", report)
        self.assertEqual(failure.exception.diagnostic()["code"], "sandbox_not_owned")
        self.assertEqual([check["name"] for check in report["checks"]], ["timeout_not_success"])
