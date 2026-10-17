"""Real Native loop over a deterministic browser port; no Chromium claim."""

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.browser_read.backend import BrowserReadResult
from app.services.browser_read.harness import BrowserNativeHarness
from app.services.harness_service import FunctionCallingHarness
from app.services.harness_types import (
    HarnessError,
    HarnessExecutionContext,
    HarnessRequest,
)
from app.services.model_contract import ModelResponse, ToolCall
from tests.services.test_browser_read_backend import wire_result
from tests.services.test_browser_read_profile import profile_fixture

EXECUTION = HarnessExecutionContext("mis-1", "wu-1", 1)


class FixtureBackend:
    def __init__(self, profile, results=()):
        self.profile, self.results, self.calls = profile, list(results), []

    async def read(self, url, *, timeout_seconds):
        self.calls.append((url, timeout_seconds))
        result = self.results.pop(0) if self.results else wire_result()
        return BrowserReadResult.model_validate(result)


class FixtureModel:
    def __init__(self, calls=1, output="Observed browser text"):
        self.calls, self.output, self.requests = calls, output, []

    async def complete(self, request):
        self.requests.append(request)
        if len(self.requests) == 1 and self.calls:
            return ModelResponse(
                tool_calls=tuple(
                    ToolCall(
                        f"read-{number}",
                        "browser_read",
                        {"url": "https://example.com/"},
                    )
                    for number in range(self.calls)
                )
            )
        return ModelResponse(content=self.output)


class BrowserHarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.profile = profile_fixture(self.root)

    def harness(self, backend=None, model=None):
        backend = backend or FixtureBackend(self.profile)
        model = model or FixtureModel()
        self.backend, self.model, self.tool_sets = backend, model, []

        def native(tools):
            self.tool_sets.append(tuple(tool.name for tool in tools))
            return FunctionCallingHarness(
                model,
                list(tools),
                model_retries=0,
                max_iterations=3,
                max_tool_calls=backend.profile.max_reads,
            )

        return BrowserNativeHarness(
            execution=EXECUTION,
            backend=backend,
            native_factory=native,
            contract_id="contract-1",
            contract_version=1,
        )

    def request(self, **updates):
        return HarnessRequest(
            **(
                {
                    "code": "Read the approved page",
                    "language": "text",
                    "timeout": 3,
                    "execution": EXECUTION,
                }
                | updates
            )
        )

    async def test_native_read_returns_real_observation_bytes_and_contract_bound_report(
        self,
    ):
        result = await self.harness().execute(self.request())
        self.assertTrue(result.sandbox.success)
        self.assertEqual(self.tool_sets, [("browser_read",)])
        self.assertEqual(
            [artifact.kind for artifact in result.artifacts], ["test-result", "report"]
        )
        report = json.loads(result.artifacts[-1].content)
        self.assertEqual(
            (report["missionId"], report["workUnitId"], report["attempt"]),
            ("mis-1", "wu-1", 1),
        )
        self.assertEqual(
            (report["contractId"], report["contractVersion"]), ("contract-1", 1)
        )
        self.assertEqual(report["reads"][0]["result"]["text"], "Observed text")
        self.assertTrue(report["cleanupConfirmed"])
        self.assertEqual(report["acceptance"], "artifact-integrity-only")
        self.assertIn("Observed text", self.model.requests[1].messages[-1].content)
        self.assertEqual(self.model.requests[1].messages[-1].role, "tool")

    async def test_model_completion_cannot_override_no_read_failed_read_or_cleanup(
        self,
    ):
        cases = (
            (FixtureModel(calls=0), []),
            (
                FixtureModel(),
                [wire_result(success=False, errorCode="http_error", httpStatus=404)],
            ),
            (FixtureModel(), [wire_result(cleanupConfirmed=False)]),
        )
        for model, results in cases:
            with self.subTest(results=results):
                result = await self.harness(
                    FixtureBackend(self.profile, results), model
                ).execute(self.request())
                self.assertFalse(result.sandbox.success)

    async def test_failed_early_read_can_be_followed_by_a_real_success(self):
        results = [
            wire_result(success=False, errorCode="http_error", httpStatus=404),
            wire_result(),
        ]
        result = await self.harness(
            FixtureBackend(self.profile, results), FixtureModel(calls=2)
        ).execute(self.request())
        self.assertTrue(result.sandbox.success)
        self.assertEqual(len(json.loads(result.artifacts[1].content)["reads"]), 2)

    async def test_model_output_limit_cannot_be_reported_as_success(self):
        self.profile = profile_fixture(self.root, max_model_output_bytes=10)
        result = await self.harness().execute(self.request())
        self.assertFalse(result.sandbox.success)
        self.assertTrue(
            json.loads(result.artifacts[-1].content)["modelOutputTruncated"]
        )

    async def test_identity_cwd_stream_and_reuse_are_rejected_before_browser_access(
        self,
    ):
        harness = self.harness()
        invalid = (
            {"execution": HarnessExecutionContext("mis-2", "wu-1", 1)},
            {"cwd": self.root},
            {"on_text_delta": lambda _: None},
            {"timeout": float("nan")},
        )
        for updates in invalid:
            with self.subTest(updates=updates), self.assertRaises(HarnessError):
                await harness.execute(self.request(**updates))
        self.assertEqual(self.backend.calls, [])
        await harness.execute(self.request())
        with self.assertRaises(HarnessError):
            await harness.execute(self.request())

    async def test_cancel_swallowing_backend_cannot_return_late_artifacts_or_another_model_call(
        self,
    ):
        started, release = asyncio.Event(), asyncio.Event()
        backend = FixtureBackend(self.profile)
        worker_tasks = []

        async def resistant(url, *, timeout_seconds):
            worker_tasks.append(asyncio.current_task())
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return BrowserReadResult.model_validate(wire_result())

        backend.read = resistant
        harness = self.harness(backend)
        task = asyncio.create_task(harness.execute(self.request()))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            await asyncio.gather(*worker_tasks, return_exceptions=True)
        self.assertEqual(len(self.model.requests), 1)

    async def test_reporting_work_cannot_return_success_after_the_deadline(self):
        from app.services.browser_read.observations import observed_result

        def delayed(*args):
            time.sleep(0.08)
            return observed_result(*args)

        with (
            patch("app.services.browser_read.harness.observed_result", delayed),
            self.assertRaises(TimeoutError),
        ):
            await self.harness().execute(self.request(timeout=0.05))
