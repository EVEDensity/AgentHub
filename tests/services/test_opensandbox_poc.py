"""PoC handoff/failure reporting tests, without any external execution."""

from __future__ import annotations

import argparse
import contextlib
import io
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


class PocTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_still_requires_later_marker_check_with_timing_margin(self):
        backend = SimpleNamespace(execute=AsyncMock(side_effect=[OpenSandboxError("command timed out"),
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

    async def test_destroyed_sandbox_does_not_count_as_a_missing_marker_success(self):
        backend = SimpleNamespace(execute=AsyncMock(side_effect=[OpenSandboxError("command timed out"),
            OpenSandboxError("not owned", code="sandbox_not_owned")]))
        report = {"checks": []}
        with (patch.object(opensandbox_poc.asyncio, "sleep", new_callable=AsyncMock),
              self.assertRaises(OpenSandboxError) as failure):
            await opensandbox_poc._timeout_check(backend, "sandbox", report)
        self.assertEqual(failure.exception.diagnostic()["code"], "sandbox_not_owned")
        self.assertEqual([check["name"] for check in report["checks"]], ["timeout_not_success"])
