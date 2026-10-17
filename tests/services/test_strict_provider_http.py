"""Real loopback HTTP + SQLite accounting, not a charging-provider claim."""

from __future__ import annotations

import asyncio
import gzip
import json
import tempfile
import traceback
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.domain.mission_budget import BudgetConflict, BudgetError, BudgetExhausted
from app.services.strict_provider import (
    BudgetedModelPort,
    StrictProviderError,
    StrictProviderReplayError,
)
from tests.services.mission_budget_fixtures import add_running_unit, execution_scope
from tests.services.strict_provider_fixtures import (
    KEY,
    LoopbackPeer,
    SQLiteBudgetFixture,
    profile,
    request,
    response,
)


class StrictProviderHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fixture = await SQLiteBudgetFixture().start(Path(directory.name))
        self.addAsyncCleanup(self.fixture.close)
        self.peer = LoopbackPeer().start()
        self.addCleanup(self.peer.close)

    def start_call(self, port, model_request):
        task = asyncio.create_task(port.complete(model_request))
        self.addAsyncCleanup(self.drain_call, task)
        return task

    async def drain_call(self, task):
        self.peer.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_single_http_has_real_dispatch_before_peer_receives_and_settles(self):
        seen = []
        loop = asyncio.get_running_loop()

        async def inspect():
            seen.append((await self.fixture.reservation()).state.value)

        self.peer.before_response = lambda: asyncio.run_coroutine_threadsafe(
            inspect(), loop
        ).result(timeout=2)
        port = self.fixture.port(self.peer)
        result = await port.complete(request())
        self.assertEqual(
            (
                result.content,
                result.usage.prompt_tokens,
                result.usage.completion_tokens,
            ),
            ("done", 10, 2),
        )
        self.assertEqual(seen, ["DISPATCHED"])
        path, headers, body = self.peer.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers["authorization"], "Bearer " + KEY)
        self.assertEqual(json.loads(body)["max_completion_tokens"], 100)
        self.assertFalse(json.loads(body)["stream"])
        account = await self.fixture.account()
        self.assertEqual((account.held_nano_usd, account.spent_nano_usd), (0, 14_000))
        reservation = await self.fixture.reservation()
        self.assertEqual(reservation.reserved_nano_usd, 100_200_000)
        self.assertNotIn(
            KEY, (await self.fixture.ledger.receipt(reservation.id)).document
        )
        self.assertNotIn(KEY, repr(port) + repr(port.recovery_manifest))

    async def test_settled_unknown_and_restarted_same_operations_never_resend(self):
        await self.fixture.port(self.peer).complete(request())
        for _ in range(2):
            with self.assertRaises(StrictProviderReplayError):
                await self.fixture.port(self.peer).complete(request())
        self.peer.payload = response(receipt="response-missing", usage=None)
        unknown_request = request("harness:model:2:response")
        with self.assertRaises(StrictProviderError):
            await self.fixture.port(self.peer).complete(unknown_request)
        with self.assertRaises(StrictProviderReplayError):
            await self.fixture.port(self.peer).complete(unknown_request)
        self.assertEqual(len(self.peer.requests), 2)
        self.assertEqual(
            (
                await self.fixture.reservation(
                    unknown_request.metadata["modelOperationId"]
                )
            ).state.value,
            "UNKNOWN",
        )

    async def test_settled_wire_and_profile_reentry_changes_conflict_without_http(self):
        await self.fixture.port(self.peer).complete(request())
        changed = request(
            messages=(replace(request().messages[0], content="different"),)
        )
        with self.assertRaises(BudgetConflict):
            await self.fixture.port(self.peer).complete(changed)
        revised = self.fixture.port(
            self.peer, context_ceiling_source="different reviewed source"
        )
        with self.assertRaises(BudgetConflict):
            await revised.complete(request())
        self.assertEqual(len(self.peer.requests), 1)

    async def test_duplicate_concurrent_dispatch_claims_send_once(self):
        self.peer.release.clear()
        first = self.start_call(self.fixture.port(self.peer), request())
        await self.peer.wait_entered()
        with self.assertRaises(StrictProviderReplayError):
            await self.fixture.port(self.peer).complete(request())
        self.peer.release.set()
        self.assertEqual((await first).content, "done")
        self.assertEqual(len(self.peer.requests), 1)

    async def test_limited_shared_mission_budget_refuses_second_work_unit_before_http(
        self,
    ):
        await self.fixture.service.initialize_account("mis-1", limit_usd="0.15")
        self.peer.release.clear()
        await add_running_unit(self.fixture.repository, "wu-child", parent="wu-1")
        first = self.start_call(
            self.fixture.port(self.peer, limit_usd="0.15"), request()
        )
        await self.peer.wait_entered()
        child = BudgetedModelPort(
            execution_scope("wu-child"),
            self.fixture.service,
            profile(self.peer.endpoint),
            KEY,
            limit_usd="0.15",
        )
        with self.assertRaises(BudgetExhausted):
            await child.complete(request())
        self.peer.release.set()
        await first
        self.assertEqual(len(self.peer.requests), 1)

    async def test_valid_usage_is_settled_before_rejecting_bad_content(self):
        self.peer.payload = response(choices="invalid-output")
        with self.assertRaisesRegex(StrictProviderError, "content is invalid"):
            await self.fixture.port(self.peer).complete(request())
        reservation = await self.fixture.reservation()
        self.assertEqual(
            (reservation.state.value, reservation.actual_nano_usd), ("SETTLED", 14_000)
        )
        self.assertEqual((await self.fixture.account()).held_nano_usd, 0)

    async def test_missing_usage_zero_usage_and_receipt_reuse_are_distinct(self):
        self.peer.payload = response(usage=None)
        with self.assertRaises(StrictProviderError):
            await self.fixture.port(self.peer).complete(request())
        unknown = await self.fixture.reservation()
        self.assertEqual(
            (unknown.state.value, unknown.unknown_reason), ("UNKNOWN", "missing_usage")
        )
        self.peer.payload = response(
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
        await self.fixture.port(self.peer).complete(request("harness:model:2:response"))
        with self.assertRaises(BudgetConflict):
            await self.fixture.port(self.peer).complete(
                request("harness:model:3:response")
            )
        account = await self.fixture.account()
        self.assertEqual(
            (account.held_nano_usd, account.spent_nano_usd), (200_400_000, 0)
        )

    async def test_http_errors_redirects_wrong_media_encoding_and_oversize_never_retry(
        self,
    ):
        cases = [
            (503, {}, b"provider error " + KEY.encode()),
            (302, {"Location": self.peer.endpoint}, b"redirect"),
            (200, {"Content-Type": "text/plain"}, b"not json"),
            (200, {"Content-Encoding": "gzip"}, gzip.compress(b"x" * 100_000)),
            (200, {}, b"x" * 2048),
        ]
        for index, (status, headers, body) in enumerate(cases, 1):
            self.peer.status, self.peer.raw_body = status, body
            self.peer.headers = {"Content-Type": "application/json", **headers}
            with (
                self.subTest(index=index),
                self.assertRaises(StrictProviderError) as caught,
            ):
                await self.fixture.port(self.peer, max_response_bytes=1024).complete(
                    request(f"call:{index}")
                )
            rendered = "".join(traceback.format_exception(caught.exception))
            self.assertNotIn(KEY, rendered)
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual(len(self.peer.requests), index)
            self.assertEqual(
                (await self.fixture.reservation(f"call:{index}")).state.value, "UNKNOWN"
            )

    async def test_timeout_and_cancellation_after_receipt_keep_hold_and_never_replay(
        self,
    ):
        self.peer.release.clear()
        task = self.start_call(self.fixture.port(self.peer), request())
        await self.peer.wait_entered()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        reservation = await self.fixture.reservation()
        self.assertEqual(
            (reservation.state.value, reservation.unknown_reason),
            ("UNKNOWN", "cancelled"),
        )
        self.peer.entered.clear()
        timed = self.start_call(
            self.fixture.port(self.peer, timeout_seconds=5),
            request("timeout:1", timeout_seconds=5),
        )
        await self.peer.wait_entered()
        with self.assertRaises(TimeoutError):
            await timed
        self.assertEqual(
            (await self.fixture.reservation("timeout:1")).unknown_reason, "timeout"
        )
        self.peer.release.set()
        with self.assertRaises(StrictProviderReplayError):
            await self.fixture.port(self.peer).complete(request())
        self.assertEqual(len(self.peer.requests), 2)

    async def test_settlement_failure_preserves_full_hold(self):
        with (
            patch.object(
                self.fixture.service,
                "settle_trusted",
                AsyncMock(side_effect=BudgetError("ledger unavailable")),
            ),
            self.assertRaises(BudgetError),
        ):
            await self.fixture.port(self.peer).complete(request())
        reservation = await self.fixture.reservation()
        self.assertEqual(reservation.state.value, "UNKNOWN")
        self.assertEqual(
            (await self.fixture.account()).held_nano_usd, reservation.reserved_nano_usd
        )
        self.assertIsNone(await self.fixture.ledger.receipt(reservation.id))

    async def test_over_reservation_records_whole_charge_and_blocks_before_return(self):
        self.peer.payload = response(
            usage={
                "prompt_tokens": 200_000,
                "completion_tokens": 2,
                "total_tokens": 200_002,
            }
        )
        with self.assertRaisesRegex(StrictProviderError, "billing bounds"):
            await self.fixture.port(self.peer).complete(request())
        account = await self.fixture.account()
        self.assertEqual(
            (account.spent_nano_usd, account.held_nano_usd, account.blocked),
            (200_004_000, 0, 1),
        )
        with self.assertRaises(BudgetExhausted):
            await self.fixture.port(self.peer).complete(request("another"))
        self.assertEqual(len(self.peer.requests), 1)

    async def test_stale_scope_never_sends_http(self):
        stale = self.fixture.scope.model_copy(update={"runner_id": "unrelated-runner"})
        with self.assertRaises(BudgetError):
            await BudgetedModelPort(
                stale, self.fixture.service, profile(self.peer.endpoint), KEY
            ).complete(request())
        self.assertEqual(self.peer.requests, [])

    async def test_provider_cannot_persist_or_return_literal_bearer(self):
        self.peer.payload = response(receipt=KEY)
        with self.assertRaisesRegex(StrictProviderError, "private credential"):
            await self.fixture.port(self.peer).complete(request())
        first = await self.fixture.reservation()
        self.assertIsNone(await self.fixture.ledger.receipt(first.id))
        self.peer.payload = response(
            receipt="safe-id",
            choices=[{"message": {"role": "assistant", "content": KEY}}],
        )
        with self.assertRaisesRegex(StrictProviderError, "private credential"):
            await self.fixture.port(self.peer).complete(request("another"))
        settled = await self.fixture.reservation("another")
        self.assertNotIn(KEY, (await self.fixture.ledger.receipt(settled.id)).document)
        self.assertEqual(settled.state.value, "SETTLED")

    async def test_inner_json_unicode_escaped_credential_cannot_become_tool_argument(
        self,
    ):
        escaped = KEY.replace("private", "\\u0070rivate")
        function = {
            "id": "call-1",
            "type": "function",
            "function": {"name": "read", "arguments": '{"value":"' + escaped + '"}'},
        }
        self.peer.payload = response(
            choices=[
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [function],
                    },
                }
            ]
        )
        with self.assertRaisesRegex(StrictProviderError, "private credential"):
            await self.fixture.port(self.peer).complete(request())
        self.assertEqual((await self.fixture.reservation()).state.value, "SETTLED")
