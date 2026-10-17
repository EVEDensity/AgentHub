"""A durable dispatch acknowledgement cannot bypass local cancellation/deadline."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.services.strict_provider_fixtures import (
    LoopbackPeer,
    SQLiteBudgetFixture,
    request,
)


class StrictProviderFenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fixture = await SQLiteBudgetFixture().start(Path(directory.name))
        self.addAsyncCleanup(self.fixture.close)
        self.peer = LoopbackPeer().start()
        self.addCleanup(self.peer.close)

    async def test_cancel_swallowed_by_committed_dispatch_ack_cannot_send_http(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.fixture.service.dispatch_once

        async def delayed_ack(*args, **kwargs):
            result = await original(*args, **kwargs)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return result

        with patch.object(self.fixture.service, "dispatch_once", delayed_ack):
            task = asyncio.create_task(self.fixture.port(self.peer).complete(request()))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                task.cancel()
                await asyncio.sleep(0)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.peer.requests, [])
        reservation = await self.fixture.reservation()
        self.assertEqual(
            (reservation.state.value, reservation.unknown_reason),
            ("UNKNOWN", "cancelled"),
        )
        self.assertEqual(
            (await self.fixture.account()).held_nano_usd, reservation.reserved_nano_usd
        )

    async def test_expired_dispatch_ack_cannot_send_even_when_cancellation_is_swallowed(
        self,
    ):
        original = self.fixture.service.dispatch_once

        async def delayed_ack(*args, **kwargs):
            result = await original(*args, **kwargs)
            try:
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                return result
            raise AssertionError(
                "operation deadline did not interrupt dispatch acknowledgement"
            )

        with (
            patch.object(self.fixture.service, "dispatch_once", delayed_ack),
            self.assertRaises(TimeoutError),
        ):
            await self.fixture.port(self.peer, timeout_seconds=0.1).complete(request())
        self.assertEqual(self.peer.requests, [])
        reservation = await self.fixture.reservation()
        self.assertEqual(
            (reservation.state.value, reservation.unknown_reason),
            ("UNKNOWN", "timeout"),
        )

    async def test_unknown_cleanup_that_swallows_cancel_cannot_hold_caller_forever(
        self,
    ):
        release = asyncio.Event()
        cleanup_tasks = []

        async def delayed_cleanup(*_args, **_kwargs):
            cleanup_tasks.append(asyncio.current_task())
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

        self.peer.release.clear()
        with patch.object(self.fixture.service, "mark_unknown", delayed_cleanup):
            task = asyncio.create_task(self.fixture.port(self.peer).complete(request()))
            try:
                await self.peer.wait_entered()
                started = asyncio.get_running_loop().time()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=1)
                self.assertLess(asyncio.get_running_loop().time() - started, 0.9)
            finally:
                self.peer.release.set()
                release.set()
                await asyncio.gather(task, *cleanup_tasks, return_exceptions=True)
        reservation = await self.fixture.reservation()
        self.assertEqual(reservation.state.value, "DISPATCHED")
        self.assertEqual(
            (await self.fixture.account()).held_nano_usd, reservation.reserved_nano_usd
        )
