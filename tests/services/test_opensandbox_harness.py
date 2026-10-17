"""Native tool-loop composition over the real adapter's mocked HTTP wire.

These tests do not claim Docker or hostile-workload isolation evidence.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import uuid
from dataclasses import replace

import httpx

from app.services.execution_artifacts import HarnessArtifact
from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.harness_service import FunctionCallingHarness
from app.services.harness_types import HarnessError, HarnessRequest, HarnessResult
from app.services.model_contract import ModelResponse, ToolCall
from app.services.opensandbox_backend import OpenSandboxBackend
from app.services.opensandbox_harness import SandboxHarnessLimits, SandboxNativeHarness
from app.services.opensandbox_protocol import WORKLOAD_IMAGE, OpenSandboxConfig
from app.services.tools.sandbox_executor import SandboxResult
from tests.services.test_opensandbox_backend import JOB, KEY, WirePeer


class NativeWirePeer(WirePeer):
    def __init__(self):
        super().__init__()
        self.shell_modes = []
        self.shell_started = asyncio.Event()
        self.created_ids = []

    def command(self, request, sandbox):
        body = json.loads(request.content) if request.method == "POST" else {}
        shell = body.get("argv", [None])[0] == "bash"
        if shell:
            self.next_mode = self.shell_modes.pop(0) if self.shell_modes else "normal"
        result = super().command(request, sandbox)
        if shell:
            self.shell_started.set()
        return result

    async def __call__(self, request):
        if request.method == "POST" and request.url.path == "/v1/sandboxes":
            assert request.headers["OPEN-SANDBOX-API-KEY"] == KEY
            self.requests.append(request)
            identity = str(uuid.uuid4())
            self.created_ids.append(identity)
            self.sandboxes[identity] = json.loads(request.content)
            return httpx.Response(
                202, json={"id": identity, "status": {"state": "Running"}}
            )
        return await super().__call__(request)


class FixtureModel:
    """Deterministic canonical ModelPort fixture, not a real provider."""

    def __init__(self, scripts, *, summary="Native model final answer"):
        self.scripts = list(scripts)
        self.summary = summary
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if self.scripts:
            sequence = len(self.requests)
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        f"call-{sequence}", "run_shell", {"script": self.scripts.pop(0)}
                    ),
                )
            )
        return ModelResponse(content=self.summary)


def plain_result(*, success=True, artifacts=()):
    return HarnessResult(
        SandboxResult(success, "plain result", "", 0 if success else 1, 0, "fixture"),
        artifacts=artifacts,
    )


class OpenSandboxNativeHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.peer = NativeWirePeer()
        self.backend = OpenSandboxBackend(
            OpenSandboxConfig("http://localhost", KEY, enabled=True),
            transport=httpx.MockTransport(self.peer),
        )
        self.addAsyncCleanup(self.backend.close)
        self.execution = HarnessExecutionContext("mission", "work-unit", 2)

    def harness(
        self,
        scripts=("printf actual-command",),
        *,
        limits=None,
        native_factory=None,
        summary="done",
    ):
        self.model = FixtureModel(scripts, summary=summary)
        factory = native_factory or (
            lambda tools: FunctionCallingHarness(
                self.model, list(tools), model_retries=0
            )
        )
        return SandboxNativeHarness(
            execution=self.execution,
            backend=self.backend,
            native_factory=factory,
            limits=limits,
            job_id=JOB,
        )

    def request(self, **changes):
        return replace(
            HarnessRequest("use shell", "text", 5, execution=self.execution), **changes
        )

    def report(self, result):
        self.assertEqual(
            [item.kind for item in result.artifacts], ["test-result", "report"]
        )
        self.assertEqual(result.artifacts[0].content, result.sandbox.stdout.encode())
        return json.loads(result.artifacts[-1].content)

    async def test_native_calls_only_attempt_shell_and_generates_bound_observations_after_cleanup(
        self,
    ):
        result = await self.harness().execute(self.request())
        self.assertTrue(result.sandbox.success)
        report = self.report(result)
        self.assertEqual(
            (report["missionId"], report["workUnitId"], report["attempt"]),
            ("mission", "work-unit", 2),
        )
        self.assertEqual(report["schema"], "agenthub.opensandbox-execution-report.v1")
        self.assertEqual(
            (report["image"], report["backendKind"], report["cleanupConfirmed"]),
            (WORKLOAD_IMAGE, "opensandbox", True),
        )
        command = report["commands"][0]
        self.assertEqual(
            (command["commandId"], command["exitCode"], command["combinedOutput"]),
            ("cmd-2", 0, "real-output"),
        )
        self.assertEqual(command["outputStream"], "combined")
        self.assertNotIn("printf actual-command", result.artifacts[-1].content.decode())
        self.assertNotIn(
            KEY, str(self.model.requests) + result.artifacts[-1].content.decode()
        )
        self.assertEqual(self.peer.sandboxes, {})
        self.assertEqual(
            [value["function"]["name"] for value in self.model.requests[0].tools],
            ["run_shell"],
        )
        commands = [
            json.loads(value.content)
            for value in self.peer.requests
            if value.method == "POST" and value.url.path.endswith("/command")
        ]
        self.assertEqual(
            commands[-1]["argv"][:4], ["bash", "--noprofile", "--norc", "-c"]
        )
        self.assertTrue(
            commands[-1]["argv"][-1].startswith("cd /workspace || exit 125\n")
        )

    async def test_last_nonzero_prevents_success_but_later_successful_repair_keeps_both_facts(
        self,
    ):
        self.peer.shell_modes = ["nonzero"]
        failed = await self.harness().execute(self.request())
        self.assertFalse(failed.sandbox.success)
        self.assertEqual(self.report(failed)["commands"][-1]["exitCode"], 7)
        self.peer.shell_modes = ["nonzero", "normal"]
        repaired = await self.harness(("exit 7", "printf repaired")).execute(
            self.request()
        )
        self.assertTrue(repaired.sandbox.success)
        self.assertEqual(
            [value["exitCode"] for value in self.report(repaired)["commands"]], [7, 0]
        )
        self.assertNotEqual(self.peer.created_ids[0], self.peer.created_ids[1])

    async def test_no_command_cannot_become_success_and_native_bundle_is_refused(self):
        no_command = await self.harness(()).execute(self.request())
        self.assertFalse(no_command.sandbox.success)
        self.assertEqual(self.report(no_command)["commands"], [])

        class OtherBundle:
            async def execute(self, _request):
                return plain_result(
                    artifacts=(
                        HarnessArtifact("other", "report", "application/json", b"{}"),
                    )
                )

        with self.assertRaisesRegex(HarnessError, "another Artifact bundle"):
            await self.harness(native_factory=lambda _: OtherBundle()).execute(
                self.request()
            )
        self.assertEqual(self.peer.sandboxes, {})

    async def test_mismatched_scope_resume_and_second_use_refuse_before_creating(self):
        harness = self.harness()
        with self.assertRaises(HarnessError):
            await harness.execute(
                self.request(execution=HarnessExecutionContext("mission", "other", 2))
            )
        with self.assertRaises(HarnessError):
            await harness.execute(self.request(resume=object()))
        self.assertEqual(self.peer.created_ids, [])
        await harness.execute(self.request())
        with self.assertRaises(HarnessError):
            await harness.execute(self.request())
        self.assertEqual(len(self.peer.created_ids), 1)

    async def test_output_cap_and_command_count_limits_cannot_be_overridden_by_summary(
        self,
    ):
        output = await self.harness(
            limits=SandboxHarnessLimits(max_output_bytes=8)
        ).execute(self.request())
        self.assertFalse(output.sandbox.success)
        command = self.report(output)["commands"][0]
        self.assertTrue(command["outputTruncated"])
        self.assertEqual(command["combinedOutput"], "real-out")
        capped = await self.harness(
            ("printf one", "printf two"), limits=SandboxHarnessLimits(max_commands=1)
        ).execute(self.request())
        self.assertFalse(capped.sandbox.success)
        self.assertEqual(len(self.report(capped)["commands"]), 1)

    async def test_manager_secret_is_redacted_from_command_model_feedback_and_final_artifacts(
        self,
    ):
        self.peer.shell_modes = ["secret"]
        result = await self.harness(summary=KEY).execute(self.request())
        self.assertTrue(result.sandbox.success)
        self.assertNotIn(KEY, str(self.model.requests) + str(result))
        self.assertEqual(result.sandbox.stdout, "[REDACTED]")
        self.assertEqual(
            self.report(result)["commands"][0]["combinedOutput"], "[REDACTED]"
        )

    async def test_timeout_is_fatal_and_cleanup_failure_cannot_return_artifacts(self):
        self.peer.shell_modes = ["running"]
        result = await self.harness(
            limits=SandboxHarnessLimits(max_command_seconds=0.02)
        ).execute(self.request())
        self.assertFalse(result.sandbox.success)
        self.assertEqual(self.report(result)["commands"][0]["status"], "backend_error")
        self.peer.failed_delete = True
        try:
            with self.assertRaisesRegex(HarnessError, "destruction was not confirmed"):
                await self.harness().execute(self.request())
        finally:
            self.peer.failed_delete = False

    async def test_cancel_after_real_wire_start_destroys_only_this_attempt_sandbox(
        self,
    ):
        unrelated = await self.backend.create(JOB)
        self.peer.shell_modes = ["running"]
        task = asyncio.create_task(self.harness().execute(self.request()))
        try:
            await asyncio.wait_for(self.peer.shell_started.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(set(self.peer.sandboxes), {unrelated})
