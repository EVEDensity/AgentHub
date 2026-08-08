from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from app.core.config import ArtifactStoreSettings
from app.services.artifact_store_service import (
    ContentAddressedArtifactPublisher,
    PublishedArtifact,
)
from app.services.runner_service import (
    MissionControlRunnerClient,
    RunnerControlError,
    RunnerExecutionError,
    WorkUnitRunner,
)
from app.services.tools.sandbox_executor import SandboxExecutor, SandboxResult
from tests.api.test_missions_api import FakeMissionRepository, build_app
from tests.domain.factories import build_contract, build_mission, build_work_unit


class FakeControl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.registered: list[dict[str, Any]] = []
        self.should_fail_reporting = False
        self.should_fail_failure = False
        self.heartbeat_error: Exception | None = None
        self.heartbeat_received = asyncio.Event()

    async def lease_work_unit(self, mission_id: str, work_unit_id: str, **kwargs: Any):
        self.calls.append(("lease", kwargs))
        return {"id": work_unit_id, "attempt": 1, "lease": {"id": "lease-1"}}

    async def start_work_unit(self, mission_id: str, work_unit_id: str, **kwargs: Any):
        self.calls.append(("start", kwargs))
        return {"id": work_unit_id, "attempt": 1, "lease": {"id": "lease-1"}}

    async def heartbeat_work_unit(
        self, mission_id: str, work_unit_id: str, **kwargs: Any
    ):
        self.calls.append(("heartbeat", kwargs))
        self.heartbeat_received.set()
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        return {"id": work_unit_id, "attempt": 1, "lease": {"id": "lease-1"}}

    async def register_artifact(
        self,
        mission_id: str,
        work_unit_id: str,
        **kwargs: Any,
    ):
        self.calls.append(("register", kwargs))
        self.registered.append(kwargs)
        if self.should_fail_reporting:
            raise RuntimeError("registration rejected")
        return {"id": kwargs["artifact_id"]}

    async def complete_work_unit(self, mission_id: str, work_unit_id: str, **kwargs: Any):
        self.calls.append(("complete", kwargs))
        if self.should_fail_reporting:
            raise RuntimeError("completion rejected")
        return {"id": work_unit_id, "status": "VERIFYING"}

    async def fail_work_unit(self, mission_id: str, work_unit_id: str, **kwargs: Any):
        self.calls.append(("fail", kwargs))
        if self.should_fail_failure:
            raise RuntimeError("failure reporting unavailable")
        return {"id": work_unit_id, "status": "FAILED"}


class FakeSandbox:
    def __init__(self, result: SandboxResult | None = None) -> None:
        self.result = result or SandboxResult(
            success=True,
            stdout="runner output\n",
            stderr="",
            exit_code=0,
            duration_ms=1,
            mode="fake",
        )

    async def execute(self, code: str, **kwargs: Any) -> SandboxResult:
        del code, kwargs
        return self.result


class BlockingSandbox(FakeSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def execute(self, code: str, **kwargs: Any) -> SandboxResult:
        del code, kwargs
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.result


class FakePublisher:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.contents: list[bytes] = []

    async def publish_bytes(self, content: bytes) -> PublishedArtifact:
        self.contents.append(content)
        if self.error is not None:
            raise self.error
        digest = hashlib.sha256(content).hexdigest()
        return PublishedArtifact(
            digest=f"sha256:{digest}",
            size_bytes=len(content),
            content_address=f"local:sha256/{digest}",
        )

    async def publish_file(self, path: Path) -> PublishedArtifact:
        return await self.publish_bytes(path.read_bytes())


class RunnerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_runner_executes_publishes_registers_and_completes(self) -> None:
        control = FakeControl()
        publisher = FakePublisher()
        runner = WorkUnitRunner(
            control,
            publisher=publisher,
            sandbox=FakeSandbox(),
            runner_id="runner-1",
        )

        result = await runner.run("mis-1", "wu-1", code="print('ignored')")

        self.assertTrue(result.success)
        self.assertIsNotNone(result.artifact)
        self.assertEqual(
            [name for name, _ in control.calls],
            ["lease", "start", "register", "complete"],
        )
        registered = control.registered[0]
        self.assertEqual(registered["lease_id"], "lease-1")
        self.assertEqual(registered["artifact"], result.artifact)
        complete = control.calls[-1][1]
        self.assertEqual(
            complete["artifact_refs"],
            [{"id": registered["artifact_id"], "digest": result.artifact.digest}],
        )

    async def test_execution_failure_is_recorded_without_publishing(self) -> None:
        control = FakeControl()
        publisher = FakePublisher()
        runner = WorkUnitRunner(
            control,
            publisher=publisher,
            sandbox=FakeSandbox(
                SandboxResult(
                    success=False,
                    stdout="",
                    stderr="tests failed",
                    exit_code=1,
                    duration_ms=3,
                    mode="fake",
                )
            ),
            runner_id="runner-1",
        )

        result = await runner.run("mis-1", "wu-1", code="raise SystemExit(1)")

        self.assertFalse(result.success)
        self.assertEqual(result.failure_reason, "tests failed")
        self.assertEqual(publisher.contents, [])
        self.assertEqual([name for name, _ in control.calls], ["lease", "start", "fail"])

    async def test_long_execution_heartbeats_before_artifact_reporting(self) -> None:
        control = FakeControl()
        publisher = FakePublisher()
        sandbox = BlockingSandbox()
        runner = WorkUnitRunner(
            control,
            publisher=publisher,
            sandbox=sandbox,
            runner_id="runner-1",
            heartbeat_interval_seconds=0.01,
        )

        run_task = asyncio.create_task(
            runner.run("mis-1", "wu-1", code="print('ignored')")
        )
        await asyncio.wait_for(control.heartbeat_received.wait(), timeout=1)
        sandbox.release.set()
        result = await run_task

        self.assertTrue(result.success)
        call_names = [name for name, _ in control.calls]
        self.assertIn("heartbeat", call_names)
        self.assertLess(call_names.index("heartbeat"), call_names.index("register"))

    async def test_heartbeat_failure_cancels_execution_and_records_failure(self) -> None:
        control = FakeControl()
        control.heartbeat_error = RunnerControlError("lease expired")
        publisher = FakePublisher()
        sandbox = BlockingSandbox()
        runner = WorkUnitRunner(
            control,
            publisher=publisher,
            sandbox=sandbox,
            runner_id="runner-1",
            heartbeat_interval_seconds=0.01,
        )

        with self.assertRaisesRegex(RunnerExecutionError, "heartbeat supervision failed"):
            await runner.run("mis-1", "wu-1", code="print('ignored')")

        self.assertTrue(sandbox.cancelled)
        self.assertEqual(publisher.contents, [])
        self.assertEqual([name for name, _ in control.calls], ["lease", "start", "heartbeat", "fail"])
        self.assertIn("heartbeat supervision failed", control.calls[-1][1]["reason"])

    async def test_caller_cancellation_records_failure_and_propagates(self) -> None:
        control = FakeControl()
        sandbox = BlockingSandbox()
        runner = WorkUnitRunner(
            control,
            publisher=FakePublisher(),
            sandbox=sandbox,
            runner_id="runner-1",
        )

        run_task = asyncio.create_task(
            runner.run("mis-1", "wu-1", code="print('ignored')")
        )
        await asyncio.wait_for(sandbox.started.wait(), timeout=1)
        run_task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await run_task

        self.assertTrue(sandbox.cancelled)
        self.assertEqual([name for name, _ in control.calls], ["lease", "start", "fail"])
        self.assertEqual(control.calls[-1][1]["reason"], "runner execution cancelled")

    async def test_caller_cancellation_propagates_when_failure_recording_fails(self) -> None:
        control = FakeControl()
        control.should_fail_failure = True
        sandbox = BlockingSandbox()
        runner = WorkUnitRunner(
            control,
            publisher=FakePublisher(),
            sandbox=sandbox,
            runner_id="runner-1",
        )

        run_task = asyncio.create_task(
            runner.run("mis-1", "wu-1", code="print('ignored')")
        )
        await asyncio.wait_for(sandbox.started.wait(), timeout=1)
        run_task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await run_task

        self.assertTrue(sandbox.cancelled)
        self.assertEqual([name for name, _ in control.calls], ["lease", "start", "fail"])

    async def test_reporting_failure_records_work_unit_failure_and_raises(self) -> None:
        control = FakeControl()
        control.should_fail_reporting = True
        runner = WorkUnitRunner(
            control,
            publisher=FakePublisher(),
            sandbox=FakeSandbox(),
            runner_id="runner-1",
        )

        with self.assertRaisesRegex(RunnerExecutionError, "artifact reporting failed"):
            await runner.run("mis-1", "wu-1", code="print('ok')")
        self.assertEqual(
            [name for name, _ in control.calls],
            ["lease", "start", "register", "fail"],
        )

    async def test_lease_mismatch_fails_before_execution(self) -> None:
        class MismatchedControl(FakeControl):
            async def start_work_unit(self, mission_id: str, work_unit_id: str, **kwargs: Any):
                self.calls.append(("start", kwargs))
                return {"id": work_unit_id, "attempt": 2, "lease": {"id": "lease-2"}}

        control = MismatchedControl()
        with self.assertRaisesRegex(RunnerControlError, "changed the WorkUnit lease"):
            await WorkUnitRunner(
                control,
                publisher=FakePublisher(),
                sandbox=FakeSandbox(),
                runner_id="runner-1",
            ).run("mis-1", "wu-1", code="print('ok')")
        self.assertEqual([name for name, _ in control.calls], ["lease", "start"])

    async def test_real_subprocess_and_local_publisher_close_the_minimal_loop(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        settings = ArtifactStoreSettings(
            backend="local",
            local_root=Path(temporary_directory.name) / "artifacts",
            publish_max_bytes=1024,
        )
        publisher = ContentAddressedArtifactPublisher(settings)
        sandbox = SandboxExecutor()
        sandbox.mode = "subprocess"
        control = FakeControl()

        result = await WorkUnitRunner(
            control,
            publisher=publisher,
            sandbox=sandbox,
            runner_id="runner-1",
        ).run("mis-1", "wu-1", code="print('real runner')")

        self.assertTrue(result.success)
        assert result.artifact is not None
        artifact_path = (
            settings.local_root
            / "sha256"
            / result.artifact.digest.removeprefix("sha256:")
        )
        self.assertEqual(artifact_path.read_text(encoding="utf-8").strip(), "real runner")

    async def test_runner_uses_real_mission_api_state_transitions(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        settings = ArtifactStoreSettings(
            backend="local",
            local_root=Path(temporary_directory.name) / "artifacts",
            publish_max_bytes=1024,
        )
        repository = FakeMissionRepository()
        repository.mission = build_mission(
            workspace_id="runner-1",
            status="RUNNING",
        )
        repository.contract = build_contract()
        repository.work_units = [build_work_unit(status="PENDING")]
        app = build_app(
            repository,
            {"id": "runner-1", "name": "Runner", "role": "developer"},
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://mission-control",
        ) as client:
            result = await WorkUnitRunner(
                MissionControlRunnerClient(
                    "http://mission-control",
                    http_client=client,
                ),
                publisher=ContentAddressedArtifactPublisher(settings),
                sandbox=FakeSandbox(),
                runner_id="runner-1",
            ).run("mis-1", "wu-1", code="print('ignored')")

        self.assertTrue(result.success)
        self.assertEqual(repository.work_units[0].status.value, "VERIFYING")
        self.assertIsNone(repository.work_units[0].lease)
        self.assertEqual(len(repository.artifacts), 1)
        self.assertEqual(repository.artifacts[0].digest, result.artifact.digest)
        self.assertEqual(
            [event.event_type for event in repository.events],
            [
                "work_unit.lifecycle.leased",
                "work_unit.lifecycle.started",
                "artifact.lifecycle.registered",
                "work_unit.lifecycle.completed",
            ],
        )


class MissionControlClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_forwards_runner_auth_and_camel_case_payload(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={"id": "wu-1", "attempt": 1, "lease": {"id": "lease-1"}},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            control = MissionControlRunnerClient(
                "http://mission-control",
                access_token="token-1",
                http_client=client,
            )
            payload = await control.lease_work_unit(
                "mis-1",
                "wu-1",
                runner_id="runner-1",
                lease_seconds=120,
            )

        self.assertEqual(payload["lease"]["id"], "lease-1")
        self.assertEqual(str(requests[0].url), "http://mission-control/api/v1/missions/mis-1/work-units/wu-1/lease")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer token-1")
        self.assertEqual(requests[0].read(), b'{"leaseSeconds":120}')

    async def test_client_sends_heartbeat_with_lease_context(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={"id": "wu-1", "attempt": 1, "lease": {"id": "lease-1"}},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            control = MissionControlRunnerClient(
                "http://mission-control",
                http_client=client,
            )
            payload = await control.heartbeat_work_unit(
                "mis-1",
                "wu-1",
                runner_id="runner-1",
                lease_id="lease-1",
                lease_seconds=120,
            )

        self.assertEqual(payload["lease"]["id"], "lease-1")
        self.assertEqual(
            str(requests[0].url),
            "http://mission-control/api/v1/missions/mis-1/work-units/wu-1/heartbeat",
        )
        self.assertEqual(
            requests[0].read(),
            b'{"leaseId":"lease-1","leaseSeconds":120}',
        )

    async def test_client_maps_control_rejection_to_runner_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(409, json={"detail": "lease expired"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            control = MissionControlRunnerClient(
                "http://mission-control",
                http_client=client,
            )
            with self.assertRaisesRegex(RunnerControlError, "lease expired"):
                await control.start_work_unit(
                    "mis-1",
                    "wu-1",
                    runner_id="runner-1",
                    lease_id="lease-1",
                )
