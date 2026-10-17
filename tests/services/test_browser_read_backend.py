"""Strict one-line worker protocol and explicit environment; no real browser."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.browser_read.backend import (
    LocalBrowserReadBackend,
    child_environment,
    parse_worker_result,
)
from app.services.browser_read.profile import CHROMIUM_VERSION, BrowserReadError, digest
from app.services.trusted_tests.process import ProcessResult
from tests.services.test_browser_read_profile import profile_fixture


def wire_result(request_id="request", url="https://example.com/", **updates):
    return {
        "schema": "agenthub.browser-read.v1",
        "requestId": request_id,
        "success": True,
        "errorCode": None,
        "requestedUrl": url,
        "finalUrl": url,
        "httpStatus": 200,
        "title": "Observed title",
        "text": "Observed text",
        "textSha256": digest(b"Observed text"),
        "textBytes": 13,
        "truncated": False,
        "blockedResourceCount": 1,
        "redirectRejected": False,
        "playwrightVersion": "1.61.1",
        "browserVersion": CHROMIUM_VERSION,
        "workerPid": 100,
        "browserPid": 101,
        "cleanupConfirmed": True,
    } | updates


def process_result(value, **updates):
    values = {
        "exit_code": 0,
        "output": json.dumps(value).encode(),
        "timed_out": False,
        "cancelled": False,
        "limit_exceeded": False,
        "elapsed_seconds": 0.01,
    }
    return ProcessResult(**(values | updates))


class BrowserBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.profile = profile_fixture(self.root)

    def parse(self, value=None, **updates):
        return parse_worker_result(
            process_result(wire_result() if value is None else value, **updates),
            request_id="request",
            url="https://example.com/",
            profile=self.profile,
            cleanup_confirmed=True,
        )

    def test_success_requires_exact_version_identity_digest_and_consistent_facts(self):
        self.assertTrue(self.parse().success)
        invalid = (
            {"requestId": "other"},
            {"textSha256": digest(b"tampered")},
            {"textBytes": 12},
            {"textBytes": 14},
            {"truncated": True},
            {"cleanupConfirmed": False},
            {"httpStatus": 404},
            {"redirectRejected": True},
            {"browserVersion": "other"},
            {"playwrightVersion": "1.60.0"},
            {"workerPid": True},
            {"finalUrl": "https://other.com/"},
        )
        for changes in invalid:
            with (
                self.subTest(changes=changes),
                self.assertRaises((BrowserReadError, ValueError)),
            ):
                self.parse(wire_result(**changes))

    def test_protocol_process_limits_and_duplicate_fields_cannot_succeed(self):
        for values in (
            {"exit_code": 1},
            {"timed_out": True},
            {"limit_exceeded": True},
            {"cancelled": True},
            {"output": b"not JSON"},
            {"output": b'{"success":true,"success":false}'},
            {"output": json.dumps(wire_result()).encode() + b"\nextra output"},
        ):
            with self.subTest(values=values), self.assertRaises(BrowserReadError):
                self.parse(**values)

    def test_legitimate_failed_read_retains_static_failure_observations(self):
        failed = wire_result(
            success=False,
            errorCode="redirect_rejected",
            httpStatus=302,
            redirectRejected=True,
            finalUrl=None,
            browserVersion="",
        )
        self.assertFalse(self.parse(failed, exit_code=1).success)

    def test_child_environment_contains_no_inherited_credentials_options_or_proxy(self):
        with patch.dict(
            os.environ,
            {
                "NODE_OPTIONS": "malicious",
                "HTTPS_PROXY": "secret",
                "API_KEY": "credential",
            },
        ):
            environment = child_environment(self.root, "owner", self.profile)
        self.assertEqual(environment["AGENTHUB_BROWSER_OWNER"], "owner")
        for name in ("NODE_OPTIONS", "HTTPS_PROXY", "API_KEY", "PYTHONPATH"):
            self.assertNotIn(name, environment)
        self.assertTrue(Path(environment["HOME"]).is_relative_to(self.root))

    async def test_fixed_argv_input_and_private_uuid_use_fresh_process_per_read(self):
        calls = []

        async def run(argv, **options):
            payload = json.loads(Path(argv[2]).read_bytes())
            calls.append((argv, payload, options))
            self.assertEqual(
                options["environment"]["AGENTHUB_BROWSER_OWNER"], payload["requestId"]
            )
            return process_result(wire_result(payload["requestId"], payload["url"]))

        with (
            patch("app.services.browser_read.backend.sys.platform", "linux"),
            patch(
                "app.services.browser_read.backend.confirmed_cleanup",
                AsyncMock(return_value=True),
            ),
        ):
            backend = LocalBrowserReadBackend(
                self.profile,
                process_backend=type("Process", (), {"run": staticmethod(run)})(),
            )
            self.assertTrue(
                (await backend.read("https://example.com", timeout_seconds=2)).success
            )
            self.assertTrue(
                (await backend.read("https://example.com/", timeout_seconds=2)).success
            )
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0][1]["requestId"], calls[1][1]["requestId"])
        self.assertEqual(
            calls[0][0][:2],
            (str(self.profile.node_path), str(self.profile.worker_dir / "worker.mjs")),
        )
        self.assertEqual(
            set(calls[0][1]),
            {
                "schemaVersion",
                "requestId",
                "url",
                "origins",
                "maxTextChars",
                "timeoutMs",
            },
        )

    async def test_runtime_tamper_and_unconfirmed_cleanup_fail_without_success(self):
        process = AsyncMock()
        with patch("app.services.browser_read.backend.sys.platform", "linux"):
            backend = LocalBrowserReadBackend(self.profile, process_backend=process)
        (self.profile.worker_dir / "worker.mjs").write_bytes(b"changed")
        with self.assertRaises(BrowserReadError):
            await backend.read("https://example.com/", timeout_seconds=2)
        process.run.assert_not_awaited()

    def test_unsupported_platform_refuses_before_any_process(self):
        with (
            patch("app.services.browser_read.backend.sys.platform", "win32"),
            self.assertRaises(BrowserReadError),
        ):
            LocalBrowserReadBackend(self.profile)
