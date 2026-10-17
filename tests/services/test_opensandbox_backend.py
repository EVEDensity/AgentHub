"""Pinned HTTP contract tests; these do not claim real Docker isolation."""

from __future__ import annotations

import asyncio
import json
import unittest
from email import policy
from email.parser import BytesParser

import httpx

from app.services.opensandbox_backend import OpenSandboxBackend
from app.services.opensandbox_protocol import (
    MAX_BODY,
    WORKLOAD_IMAGE,
    OpenSandboxConfig,
    OpenSandboxError,
    digest,
    endpoint,
    execution_from_sse,
    terminal_status,
    upstream_error_code,
    workspace_path,
)

KEY = "unit-test-ephemeral-auth"
JOB = "6a442780-e632-4c2e-a0ec-6611905696bf"
FIRST = "38fd6aac-b49c-4e61-bdef-64781414c94d"
SECOND = "e4776bca-f8cc-41f5-8669-5a32391f743d"


class WirePeer:
    def __init__(self):
        self.sandboxes = {}
        self.commands = {}
        self.files = {}
        self.requests = []
        self.next_mode = "normal"
        self.started = asyncio.Event()
        self.failed_delete = False
        self.sequence = 0

    def command(self, request, sandbox):
        if request.method == "DELETE":
            command = self.commands[request.url.params["id"]]
            command["running"] = False
            command["exit"] = 137
            return httpx.Response(200)
        body = json.loads(request.content)
        assert "argv" in body and "command" not in body
        assert body["background"] is True
        self.sequence += 1
        identity = f"cmd-{self.sequence}"
        mode, self.next_mode = self.next_mode, "normal"
        code = 7 if body["argv"] == ["exit-seven"] else 0
        self.commands[identity] = {"sandbox": sandbox, "running": mode == "running",
                                   "exit": code, "mode": mode}
        self.started.set()
        payload = {"type": "init", "text": identity, "timestamp": 1}
        data = "data: " + json.dumps(payload) + "\n\n"
        if mode == "dropped":
            data = ""
        return httpx.Response(200, text=data, headers={"Content-Type": "text/event-stream"})

    def command_detail(self, path):
        identity = path[-1] if path[-2] == "status" else path[-2]
        command = self.commands[identity]
        if path[-1] == "logs":
            output = KEY if command["mode"] == "secret" else "real-output"
            if command["mode"] == "oversized":
                output = "x" * (MAX_BODY + 1)
            return httpx.Response(200, text=output)
        result = {"id": identity, "running": command["running"],
                  "exit_code": None if command["running"] else command["exit"],
                  "finished_at": None if command["running"] else "2026-10-17T01:00:00Z"}
        if command["mode"] == "wrong-identity":
            result["id"] = "other-command"
        if command["mode"] == "unknown-exit":
            result["exit_code"] = None
        return httpx.Response(200, json=result)

    def file(self, request, sandbox, operation):
        if operation == "download":
            return httpx.Response(200, content=self.files[(sandbox, request.url.params["path"])])
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + request.headers["content-type"].encode()
            + b"\r\nMIME-Version: 1.0\r\n\r\n" + request.content)
        parts = list(message.iter_parts())
        assert [part.get_param("name", header="content-disposition") for part in parts] == ["metadata", "file"]
        metadata = json.loads(parts[0].get_payload(decode=True))
        self.files[(sandbox, metadata["path"])] = parts[1].get_payload(decode=True)
        return httpx.Response(200)

    async def __call__(self, request):
        assert request.headers["OPEN-SANDBOX-API-KEY"] == KEY
        self.requests.append(request)
        path = request.url.path.strip("/").split("/")
        if len(path) == 2 and request.method == "POST":
            identity = FIRST if not self.sandboxes else SECOND
            self.sandboxes[identity] = json.loads(request.content)
            return httpx.Response(202, json={"id": identity, "status": {"state": "Running"}})
        sandbox = path[2]
        if len(path) == 3:
            if request.method == "DELETE":
                if self.failed_delete:
                    return httpx.Response(503, text=KEY)
                self.sandboxes.pop(sandbox, None)
                return httpx.Response(204)
            if sandbox not in self.sandboxes:
                return httpx.Response(404)
            return httpx.Response(200, json={"id": sandbox, "status": {"state": "Running"}})
        if path[-1] == "ping":
            return httpx.Response(200)
        if path[-1] == "command":
            return self.command(request, sandbox)
        if "command" in path:
            return self.command_detail(path)
        if "files" in path:
            return self.file(request, sandbox, path[-1])
        if path[-1] == "networkpolicy":
            return httpx.Response(200, json={"policy": {"defaultAction": "deny", "egress": []}})
        raise AssertionError("unexpected request")


class OpenSandboxProtocolTests(unittest.TestCase):
    def test_default_off_and_key_is_not_in_repr(self):
        config = OpenSandboxConfig("http://127.0.0.1:18080", KEY)
        self.assertNotIn(KEY, repr(config))
        with self.assertRaisesRegex(OpenSandboxError, "disabled"):
            OpenSandboxBackend(config)

    def test_endpoint_must_be_explicit_trusted_transport(self):
        for value in ("http://public.example", "https://user:secret@example.com", "https://x/?token=secret",
                      "https://x/#secret", "https://x/v1", "ftp://localhost", "http://[broken"):
            with self.subTest(value=value), self.assertRaises(OpenSandboxError):
                endpoint(value)
        self.assertEqual(endpoint("https://sandbox.example/"), "https://sandbox.example")
        self.assertEqual(endpoint("http://[::1]:18080"), "http://[::1]:18080")

    def test_auth_and_deadlines_fail_before_client_creation(self):
        for values in ({"api_key": ""}, {"api_key": "a\nb"}, {"request_timeout_seconds": float("nan")},
                       {"lifetime_seconds": 0}, {"ready_timeout_seconds": 301}):
            with self.subTest(values=values), self.assertRaises(OpenSandboxError):
                OpenSandboxConfig(**{"endpoint": "http://localhost", "api_key": KEY, **values})

    def test_workspace_path_refuses_traversal_and_platform_forms(self):
        for value in ("/etc/passwd", "../x", "a/../b", "a//b", "a/./b", "x\\y", "", "x\x00"):
            with self.subTest(value=value), self.assertRaises(OpenSandboxError):
                workspace_path(value)
        self.assertEqual(workspace_path("nested/file.txt"), "/workspace/nested/file.txt")

    def test_sse_requires_exactly_one_identity_and_no_error(self):
        for data in (b"", b"data: broken\n\n", b'data: {"type":"init","text":"../x"}\n\n',
                     b'data: {"type":"error","error":{"evalue":"secret"}}\n\n',
                     b'data: {"type":"init","text":"a"}\n\ndata: {"type":"init","text":"b"}\n\n'):
            with self.subTest(data=data), self.assertRaises(OpenSandboxError):
                execution_from_sse(data)

    def test_terminal_needs_identity_boolean_exit_and_timestamp(self):
        base = {"id": "cmd-1", "running": False, "exit_code": 0, "finished_at": "2026-10-17T01:00:00Z"}
        for overrides in ({"id": "other"}, {"running": "false"}, {"exit_code": None},
                          {"exit_code": True}, {"finished_at": None}, {"finished_at": "bad"}):
            with self.subTest(overrides=overrides), self.assertRaises(OpenSandboxError):
                terminal_status({**base, **overrides}, "cmd-1")

    def test_diagnostics_never_echo_unrecognized_codes_messages_or_urls(self):
        for body in (b"not JSON secret", b'[]', b'{"code": ["secret"]}',
                     b'{"code": "DOCKER::SECRET", "message": "secret"}'):
            self.assertIsNone(upstream_error_code(body))
        error = OpenSandboxError(KEY, code=KEY, upstream_code=KEY, operation="https://secret.example")
        self.assertEqual(error.diagnostic(), {"code": "backend_failure", "operation": "unknown"})


class OpenSandboxBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.peer = WirePeer()
        self.backend = OpenSandboxBackend(OpenSandboxConfig("http://127.0.0.1:18080", KEY, enabled=True),
                                          transport=httpx.MockTransport(self.peer))
        self.addAsyncCleanup(self.backend.close)

    async def test_create_is_pinned_deny_default_and_has_no_host_volume(self):
        sandbox = await self.backend.create(JOB)
        body = self.peer.sandboxes[sandbox]
        self.assertEqual(body["image"]["uri"], WORKLOAD_IMAGE)
        self.assertEqual(body["networkPolicy"], {"defaultAction": "deny", "egress": []})
        self.assertEqual(body["metadata"], {"agenthub.job": JOB})
        self.assertNotIn("volumes", body)
        self.assertNotIn("env", body)
        self.assertEqual((await self.backend.network_policy(sandbox))["policy"]["defaultAction"], "deny")

    async def test_preserves_real_nonzero_status_and_uses_authenticated_proxy(self):
        sandbox = await self.backend.create(JOB)
        result = await self.backend.execute(sandbox, ["exit-seven"])
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.output, "real-output")
        self.assertTrue(any("/proxy/44772/command/status/" in str(r.url) for r in self.peer.requests))

    async def test_transfer_roundtrip_digest_and_scope(self):
        first = await self.backend.create(JOB)
        second = await self.backend.create(JOB)
        receipt = await self.backend.upload(first, "input.txt", b"first", sha256=digest(b"first"))
        await self.backend.upload(second, "input.txt", b"second", sha256=digest(b"second"))
        self.assertEqual(receipt.size_bytes, 5)
        self.assertEqual(await self.backend.download(first, "input.txt", sha256=digest(b"first")), b"first")
        with self.assertRaisesRegex(OpenSandboxError, "digest mismatch"):
            await self.backend.download(first, "input.txt", sha256=digest(b"tampered"))

    async def test_invalid_upload_is_rejected_before_io(self):
        sandbox = await self.backend.create(JOB)
        count = len(self.peer.requests)
        with self.assertRaises(OpenSandboxError):
            await self.backend.upload(sandbox, "a", b"wrong", sha256=digest(b"expected"))
        self.assertEqual(len(self.peer.requests), count)

    async def test_lost_start_confirmation_destroys_without_fallback(self):
        sandbox = await self.backend.create(JOB)
        self.peer.next_mode = "dropped"
        with self.assertRaisesRegex(OpenSandboxError, "identity"):
            await self.backend.execute(sandbox, ["start"])
        self.assertNotIn(sandbox, self.peer.sandboxes)

    async def test_task_cancellation_stops_and_confirms_command(self):
        sandbox = await self.backend.create(JOB)
        self.peer.started.clear()
        self.peer.next_mode = "running"
        task = asyncio.create_task(self.backend.execute(sandbox, ["long-run"]))
        await self.peer.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.peer.commands[f"cmd-{self.peer.sequence}"]["running"])
        self.assertTrue(any(r.method == "DELETE" and r.url.params.get("id") for r in self.peer.requests))

    async def test_timeout_stops_and_confirms_command(self):
        sandbox = await self.backend.create(JOB)
        self.peer.next_mode = "running"
        with self.assertRaisesRegex(OpenSandboxError, "command timed out"):
            await self.backend.execute(sandbox, ["long-run"], timeout_seconds=0.01)
        self.assertFalse(self.peer.commands[f"cmd-{self.peer.sequence}"]["running"])

    async def test_transfer_refuses_active_command(self):
        sandbox = await self.backend.create(JOB)
        self.peer.next_mode = "running"
        execution = await self.backend.start_command(sandbox, ["long-run"])
        with self.assertRaisesRegex(OpenSandboxError, "while a command"):
            await self.backend.download(sandbox, "a")
        await self.backend.interrupt(sandbox, execution)

    async def test_unknown_terminal_exit_is_never_zero(self):
        sandbox = await self.backend.create(JOB)
        self.peer.next_mode = "unknown-exit"
        with self.assertRaises(OpenSandboxError):
            await self.backend.execute(sandbox, ["unknown"])

    async def test_mismatched_execution_identity_is_rejected(self):
        sandbox = await self.backend.create(JOB)
        self.peer.next_mode = "wrong-identity"
        with self.assertRaises(OpenSandboxError):
            await self.backend.execute(sandbox, ["wrong"])
        self.assertNotIn(sandbox, self.peer.sandboxes)

    async def test_output_limit_and_known_secret_redaction(self):
        sandbox = await self.backend.create(JOB)
        self.peer.next_mode = "secret"
        result = await self.backend.execute(sandbox, ["secret"])
        self.assertEqual(result.output, "[REDACTED]")
        self.peer.next_mode = "oversized"
        with self.assertRaisesRegex(OpenSandboxError, "limit"):
            await self.backend.execute(sandbox, ["too-large"])

    async def test_destroy_requires_confirmed_absence(self):
        sandbox = await self.backend.create(JOB)
        self.peer.failed_delete = True
        with self.assertRaises(OpenSandboxError) as failure:
            await self.backend.destroy(sandbox)
        self.assertNotIn(KEY, str(failure.exception))
        self.peer.failed_delete = False
        await self.backend.destroy(sandbox)
        self.assertNotIn(sandbox, self.peer.sandboxes)

    async def test_redirect_does_not_send_key_to_another_origin(self):
        requests = []

        def redirect(request):
            requests.append(request)
            return httpx.Response(307, headers={"Location": "https://evil.example/"})

        backend = OpenSandboxBackend(OpenSandboxConfig("http://localhost", KEY, enabled=True),
                                     transport=httpx.MockTransport(redirect))
        try:
            with self.assertRaisesRegex(OpenSandboxError, "HTTP 307"):
                await backend.create(JOB)
        finally:
            await backend.close()
        self.assertEqual(len(requests), 1)

    async def test_transport_failure_is_sanitized(self):
        def unavailable(request):
            raise httpx.ConnectError(KEY, request=request)

        backend = OpenSandboxBackend(OpenSandboxConfig("http://localhost", KEY, enabled=True),
                                     transport=httpx.MockTransport(unavailable))
        try:
            with self.assertRaises(OpenSandboxError) as failure:
                await backend.create(JOB)
            self.assertNotIn(KEY, str(failure.exception))
            self.assertEqual(failure.exception.diagnostic(),
                             {"code": "transport_error", "operation": "sandbox_create"})
        finally:
            await backend.close()

    async def test_http_failure_reports_only_allowlisted_upstream_code_and_status(self):
        for upstream, expected in (("DOCKER::SANDBOX_START_FAILED", "DOCKER::SANDBOX_START_FAILED"),
                                   (KEY, None)):
            with self.subTest(upstream=upstream):
                backend = OpenSandboxBackend(OpenSandboxConfig("http://localhost", KEY, enabled=True),
                    transport=httpx.MockTransport(lambda request, upstream=upstream: httpx.Response(
                        500, json={"code": upstream, "message": KEY, "detail": {"request": KEY}})))
                try:
                    with self.assertRaises(OpenSandboxError) as failure:
                        await backend.create(JOB)
                    diagnostic = failure.exception.diagnostic()
                    self.assertEqual(diagnostic["code"], "http_error")
                    self.assertEqual(diagnostic["operation"], "sandbox_create")
                    self.assertEqual(diagnostic["http_status"], 500)
                    self.assertEqual(diagnostic.get("upstream_code"), expected)
                    self.assertNotIn(KEY, repr(diagnostic))
                finally:
                    await backend.close()

    async def test_create_has_independent_deadline_without_extending_command_requests(self):
        async def delayed_create(request):
            if request.method == "POST" and request.url.path == "/v1/sandboxes":
                await asyncio.sleep(0.03)
            return await self.peer(request)

        backend = OpenSandboxBackend(OpenSandboxConfig("http://localhost", KEY, enabled=True,
            request_timeout_seconds=0.01, create_timeout_seconds=0.1),
            transport=httpx.MockTransport(delayed_create))
        try:
            sandbox = await backend.create(JOB)
            self.assertEqual(sandbox, FIRST)
            self.assertEqual(self.peer.requests[0].extensions["timeout"]["read"], 0.1)
            self.assertTrue(any(request.extensions["timeout"]["read"] == 0.01
                                for request in self.peer.requests[1:]))
        finally:
            await backend.close()

    async def test_create_deadline_is_enforced_and_reports_specific_operation(self):
        async def never_finishes(request):
            await asyncio.sleep(1)
            raise AssertionError("deadline was not enforced")

        backend = OpenSandboxBackend(OpenSandboxConfig("http://localhost", KEY, enabled=True,
            create_timeout_seconds=0.01), transport=httpx.MockTransport(never_finishes))
        try:
            with self.assertRaises(OpenSandboxError) as failure:
                async with asyncio.timeout(0.2):
                    await backend.create(JOB)
            self.assertEqual(failure.exception.diagnostic(),
                             {"code": "request_timeout", "operation": "sandbox_create"})
        finally:
            await backend.close()

    async def test_readiness_failure_diagnostic_is_distinct_from_create_failure(self):
        async def readiness_fails(request):
            if request.method == "GET" and request.url.path == f"/v1/sandboxes/{FIRST}" and FIRST in self.peer.sandboxes:
                return httpx.Response(500, json={"code": "DOCKER::SANDBOX_QUERY_FAILED", "message": KEY})
            return await self.peer(request)

        backend = OpenSandboxBackend(OpenSandboxConfig("http://localhost", KEY, enabled=True),
                                     transport=httpx.MockTransport(readiness_fails))
        try:
            with self.assertRaises(OpenSandboxError) as failure:
                await backend.create(JOB)
            self.assertEqual(failure.exception.diagnostic()["operation"], "sandbox_get")
            self.assertNotIn(FIRST, self.peer.sandboxes)
        finally:
            await backend.close()

    async def test_create_cleanup_failure_does_not_mask_original_readiness_error(self):
        async def readiness_fails(request):
            if (request.method == "GET" and request.url.path == f"/v1/sandboxes/{FIRST}"
                    and FIRST in self.peer.sandboxes):
                return httpx.Response(500, json={"code": "DOCKER::SANDBOX_QUERY_FAILED", "message": KEY})
            return await self.peer(request)

        self.peer.failed_delete = True
        backend = OpenSandboxBackend(OpenSandboxConfig("http://localhost", KEY, enabled=True),
                                     transport=httpx.MockTransport(readiness_fails))
        try:
            with self.assertRaises(OpenSandboxError) as failure:
                await backend.create(JOB)
            diagnostic = failure.exception.diagnostic()
            self.assertEqual(diagnostic["upstream_code"], "DOCKER::SANDBOX_QUERY_FAILED")
            self.assertEqual(diagnostic["operation"], "sandbox_get")
            self.assertEqual(diagnostic["cleanup"]["operation"], "sandbox_destroy")
            self.assertNotIn(KEY, repr(diagnostic))
        finally:
            self.peer.failed_delete = False
            await backend.close()

    async def test_execute_cleanup_failure_does_not_mask_original_command_error(self):
        failure_enabled = False

        async def command_fails(request):
            if failure_enabled and request.method == "POST" and request.url.path.endswith("/command"):
                return httpx.Response(503, json={"code": "SANDBOX::INTERNAL_ERROR", "message": KEY})
            return await self.peer(request)

        backend = OpenSandboxBackend(OpenSandboxConfig("http://localhost", KEY, enabled=True),
                                     transport=httpx.MockTransport(command_fails))
        try:
            sandbox = await backend.create(JOB)
            failure_enabled = self.peer.failed_delete = True
            with self.assertRaises(OpenSandboxError) as failure:
                await backend.execute(sandbox, ["start"])
            diagnostic = failure.exception.diagnostic()
            self.assertEqual(diagnostic["http_status"], 503)
            self.assertEqual(diagnostic["operation"], "command_start")
            self.assertEqual(diagnostic["cleanup"]["http_status"], 503)
            self.assertEqual(diagnostic["cleanup"]["operation"], "sandbox_destroy")
        finally:
            self.peer.failed_delete = False
            await backend.close()

    async def test_readiness_ping_timeout_preserves_last_status_without_body(self):
        async def ping_fails(request):
            if request.url.path.endswith("/ping"):
                return httpx.Response(502, text=KEY)
            return await self.peer(request)

        backend = OpenSandboxBackend(OpenSandboxConfig("http://localhost", KEY, enabled=True,
            ready_timeout_seconds=0.01), transport=httpx.MockTransport(ping_fails))
        try:
            with self.assertRaises(OpenSandboxError) as failure:
                await backend.create(JOB)
            diagnostic = failure.exception.diagnostic()
            self.assertEqual(diagnostic, {"code": "request_timeout", "operation": "sandbox_ping", "http_status": 502})
            self.assertNotIn(KEY, repr(diagnostic))
        finally:
            await backend.close()
