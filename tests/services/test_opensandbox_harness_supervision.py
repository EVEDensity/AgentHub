"""Bounded local fences with mocked backend wire, not real Docker evidence."""

from __future__ import annotations

import asyncio
import unittest

import httpx

from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.harness_types import HarnessError, HarnessRequest
from app.services.opensandbox_backend import OpenSandboxBackend
from app.services.opensandbox_harness import SandboxHarnessLimits, SandboxNativeHarness
from app.services.opensandbox_protocol import OpenSandboxConfig
from tests.services.test_opensandbox_backend import JOB, KEY
from tests.services.test_opensandbox_harness import NativeWirePeer, plain_result


class DelayedDeletePeer(NativeWirePeer):
    def __init__(self):
        super().__init__()
        self.delete_calls = 0

    async def __call__(self, request):
        lifecycle_delete = (
            request.method == "DELETE"
            and len(request.url.path.strip("/").split("/")) == 3
        )
        if lifecycle_delete:
            self.delete_calls += 1
            if self.delete_calls == 1:
                await asyncio.sleep(10)
        return await super().__call__(request)


class OpenSandboxHarnessSupervisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.execution = HarnessExecutionContext("mission", "work-unit", 1)

    def backend(self, peer):
        value = OpenSandboxBackend(
            OpenSandboxConfig("http://localhost", KEY, enabled=True),
            transport=httpx.MockTransport(peer),
        )
        self.addAsyncCleanup(value.close)
        return value

    def harness(self, backend, native, *, limits=None):
        return SandboxNativeHarness(
            execution=self.execution,
            backend=backend,
            native_factory=lambda _: native,
            limits=limits,
            job_id=JOB,
        )

    def request(self, timeout=3):
        return HarnessRequest("task", "text", timeout, execution=self.execution)

    async def test_cancel_swallowed_by_native_cannot_return_late_artifacts(self):
        entered, release = asyncio.Event(), asyncio.Event()
        native_tasks = []

        class LateNative:
            async def execute(self, _request):
                native_tasks.append(asyncio.current_task())
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return plain_result()

        peer = NativeWirePeer()
        task = asyncio.create_task(
            self.harness(self.backend(peer), LateNative()).execute(self.request())
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            self.assertEqual(peer.sandboxes, {})
        finally:
            release.set()
            await asyncio.gather(task, *native_tasks, return_exceptions=True)

    async def test_expired_native_cannot_return_success_and_owned_sandbox_is_removed(
        self,
    ):
        class Waiting:
            async def execute(self, _request):
                await asyncio.sleep(10)
                return plain_result()

        peer = NativeWirePeer()
        with self.assertRaises(TimeoutError):
            await self.harness(self.backend(peer), Waiting()).execute(
                self.request(0.03)
            )
        self.assertEqual(len(peer.created_ids), 1)
        self.assertEqual(peer.sandboxes, {})

    async def test_slow_destroy_preserves_failure_and_retries_only_same_owned_sandbox(
        self,
    ):
        class Completed:
            async def execute(self, _request):
                return plain_result()

        peer = DelayedDeletePeer()
        limits = SandboxHarnessLimits(cleanup_timeout_seconds=0.02)
        with self.assertRaisesRegex(HarnessError, "destruction was not confirmed"):
            await self.harness(self.backend(peer), Completed(), limits=limits).execute(
                self.request()
            )
        self.assertEqual(peer.delete_calls, 2)
        self.assertEqual(peer.sandboxes, {})
