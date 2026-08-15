from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.services.runner_worker import RunnerWorkerSnapshot
from services.python.runner_service.config import RunnerServiceSettings
from services.python.runner_service.main import create_app
from services.python.runner_service.runtime import (
    RunnerServiceRuntime,
    build_runner_runtime,
)


def _settings() -> RunnerServiceSettings:
    root = Path.cwd().resolve()
    return RunnerServiceSettings(
        runner_id="runner-1",
        workspace_id="workspace-1",
        assigned_agent_id="agent-1",
        assigned_adapter="local",
        mission_control_url="https://control.example.test",
        mission_control_token_file=root / "control.token",
        model_gateway_url="https://models.example.test/v1",
        model_gateway_token_file=root / "model.token",
        model="gpt-5-mini",
        mcp_endpoint="https://mcp.example.test/mcp/rpc",
        mcp_token_file=root / "mcp.token",
        mcp_bindings_file=root / "bindings.json",
        artifact_local_root=root / "artifacts",
    )


class FakeWorker:
    def __init__(self, *, stop_on_request: bool = True) -> None:
        self._snapshot = RunnerWorkerSnapshot()
        self._release = asyncio.Event()
        self.stop_on_request = stop_on_request
        self.stop_requests = 0
        self.cancelled = False

    @property
    def snapshot(self) -> RunnerWorkerSnapshot:
        return self._snapshot

    async def run(self) -> None:
        self._snapshot = replace(self._snapshot, running=True, ready=True)
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self._snapshot = replace(self._snapshot, running=False, ready=False)

    def request_stop(self) -> None:
        self.stop_requests += 1
        self._snapshot = replace(self._snapshot, stop_requested=True)
        if self.stop_on_request:
            self._release.set()


class CloseRecorder:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class RunnerServiceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_composition_loads_file_backed_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name in ("control.token", "model.token", "mcp.token"):
                (root / name).write_text(f"{name}-value\n", encoding="utf-8")
            (root / "bindings.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "agenthub.runner.mcp-bindings.v1",
                        "bindings": [],
                    }
                ),
                encoding="utf-8",
            )
            settings = _settings().model_copy(
                update={
                    "mission_control_token_file": root / "control.token",
                    "model_gateway_token_file": root / "model.token",
                    "mcp_token_file": root / "mcp.token",
                    "mcp_bindings_file": root / "bindings.json",
                    "artifact_local_root": root / "artifacts",
                }
            )

            runtime = build_runner_runtime(settings)
            self.assertFalse(runtime.healthy)
            self.assertFalse(runtime.ready)
            await runtime.stop()
            self.assertTrue(
                all(client.is_closed for client in runtime.closeables)
            )

    async def test_graceful_stop_waits_for_worker_then_closes_resources(self) -> None:
        worker = FakeWorker()
        closeable = CloseRecorder()
        runtime = RunnerServiceRuntime(
            worker=worker,
            shutdown_timeout_seconds=1,
            closeables=(closeable,),
        )

        await runtime.start()
        self.assertTrue(runtime.healthy)
        self.assertTrue(runtime.ready)
        await runtime.stop()

        self.assertEqual(worker.stop_requests, 1)
        self.assertFalse(worker.cancelled)
        self.assertTrue(closeable.closed)
        self.assertFalse(runtime.healthy)

    async def test_shutdown_deadline_cancels_a_stuck_worker(self) -> None:
        worker = FakeWorker(stop_on_request=False)
        runtime = RunnerServiceRuntime(
            worker=worker,
            shutdown_timeout_seconds=0.01,
        )

        await runtime.start()
        await runtime.stop()

        self.assertEqual(worker.stop_requests, 1)
        self.assertTrue(worker.cancelled)
        self.assertFalse(runtime.healthy)


class RunnerServiceEndpointTests(unittest.TestCase):
    def test_health_and_readiness_expose_only_operational_state(self) -> None:
        worker = FakeWorker()
        runtime = RunnerServiceRuntime(
            worker=worker,
            shutdown_timeout_seconds=1,
        )
        application = create_app(
            _settings(),
            runtime_factory=lambda _: runtime,
        )

        with TestClient(application) as client:
            health = client.get("/healthz")
            ready = client.get("/readyz")

            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["status"], "ok")
            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json()["status"], "ready")
            rendered = ready.text
            self.assertNotIn("workspace-1", rendered)
            self.assertNotIn("agent-1", rendered)
            self.assertNotIn("control.example", rendered)

        self.assertEqual(worker.stop_requests, 1)


if __name__ == "__main__":
    unittest.main()
