from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.services.harness_service import (
    FunctionTool,
    HarnessRequest,
    ModelResponse,
)
from app.services.runner_worker import RunnerWorkerSnapshot
from app.services.workspace_admission_service import WorkspaceClaimStatus
from services.python.runner_service.config import RunnerServiceSettings
from services.python.runner_service.main import create_app
from services.python.runner_service.runtime import (
    RunnerServiceRuntime,
    build_runner_runtime,
    compose_kind_aware_runner_runtime,
)
from tests.services.test_runner_service import (
    FakePublisher,
    mission_fork_execution_context,
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


class _ForkWorkspaceControl:
    def __init__(self, context: dict[str, Any]) -> None:
        self.context = context
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.completed = asyncio.Event()
        self.claimed = False

    async def claim_ready_work_unit(self, workspace_id: str, **kwargs: Any):
        self.calls.append(("claim", {"workspace_id": workspace_id, **kwargs}))
        if self.claimed:
            return {"claimStatus": "idle", "workUnit": None}
        self.claimed = True
        return {"claimStatus": "claimed", "workUnit": self.context["workUnit"]}

    async def get_execution_context(
        self,
        mission_id: str,
        work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("context", kwargs))
        if (mission_id, work_unit_id) != ("mis-fork", "wu-fork"):
            raise AssertionError("fork execution context identity drifted")
        return {"executionContext": self.context}

    async def start_work_unit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args
        self.calls.append(("start", kwargs))
        started = dict(self.context["workUnit"])
        started["status"] = "RUNNING"
        return started

    async def record_execution_checkpoint(
        self,
        mission_id: str,
        work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("checkpoint", kwargs))
        return {
            "id": kwargs["checkpoint_id"],
            "missionId": mission_id,
            "workUnitId": work_unit_id,
            "attempt": 1,
            "sequence": kwargs["sequence"],
            "phase": kwargs["phase"],
            "iteration": kwargs["iteration"],
            "toolCalls": kwargs["tool_calls"],
            "promptTokens": kwargs["prompt_tokens"],
            "completionTokens": kwargs["completion_tokens"],
            "modelCost": kwargs["model_cost"],
            "terminal": kwargs["terminal"],
            "failureReason": kwargs.get("failure_reason"),
            "stateDigest": "sha256:" + "a" * 64,
            "createdBy": {"id": "runner-1", "type": "service"},
            "createdAt": "2026-08-21T00:00:00+00:00",
        }

    async def heartbeat_work_unit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        heartbeat = dict(self.context["workUnit"])
        heartbeat["status"] = "RUNNING"
        return heartbeat

    async def register_artifact(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args
        self.calls.append(("register", kwargs))
        return {"id": kwargs["artifact_id"]}

    async def complete_work_unit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args
        self.calls.append(("complete", kwargs))
        self.completed.set()
        return {"id": "wu-fork", "status": "VERIFYING"}

    async def fail_work_unit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args
        self.calls.append(("fail", kwargs))
        return {"id": "wu-fork", "status": "FAILED"}


class _FinalForkModel:
    async def complete(
        self,
        request: HarnessRequest,
        tool_results: tuple[object, ...],
    ) -> ModelResponse:
        del request, tool_results
        return ModelResponse(content="fork runtime result")


class _FinalForkModelFactory:
    def build(self, tools: Sequence[FunctionTool]) -> _FinalForkModel:
        del tools
        return _FinalForkModel()


class _EmptyBindingFactory:
    def build(self, execution: object) -> Sequence[object]:
        del execution
        return ()


class RunnerServiceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_kind_aware_runtime_smoke_executes_mission_fork(self) -> None:
        context = mission_fork_execution_context()
        context["workUnit"]["dependencies"] = []
        context["workUnit"]["requiredCapabilities"] = []
        del context["workUnit"]["inputRefs"][0]["contentAddress"]
        control = _ForkWorkspaceControl(context)
        publisher = FakePublisher()
        runtime = compose_kind_aware_runner_runtime(
            control,  # type: ignore[arg-type]
            publisher=publisher,
            model_factory=_FinalForkModelFactory(),
            binding_factory=_EmptyBindingFactory(),
            runner_id="runner-1",
            workspace_id="workspace-1",
            assigned_agent_id="reviewer",
            assigned_adapter="local_codex",
            idle_delay_seconds=0.01,
            max_delay_seconds=0.02,
        )

        await runtime.start()
        try:
            await asyncio.wait_for(control.completed.wait(), timeout=1)
        finally:
            await runtime.stop()

        claim = control.calls[0]
        self.assertEqual(claim[0], "claim")
        self.assertEqual(claim[1]["workspace_id"], "workspace-1")
        self.assertEqual(
            claim[1]["supported_work_unit_kinds"],
            ("a2a.inbound", "mission.fork"),
        )
        self.assertEqual([name for name, _ in control.calls][1:], [
            "context",
            "start",
            # P3-4b: state-identical checkpoints on an unchanged zero-usage
            # state are skipped before upload; only EXECUTION_STARTED,
            # ITERATION_STARTED and the terminal EXECUTION_COMPLETED remain.
            "checkpoint",
            "checkpoint",
            "checkpoint",
            "register",
            "complete",
        ])
        self.assertEqual(publisher.contents, [b"fork runtime result"])

    async def test_strict_composition_selects_kind_aware_workspace_runner(
        self,
    ) -> None:
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
            composed_runner = object()
            with patch(
                "services.python.runner_service.runtime."
                "build_kind_aware_workspace_runner",
                return_value=composed_runner,
            ) as builder:
                runtime = build_runner_runtime(settings)

            self.assertIs(runtime.worker._runner, composed_runner)
            self.assertEqual(builder.call_args.kwargs["runner_id"], "runner-1")
            self.assertEqual(
                builder.call_args.kwargs["assigned_agent_id"],
                "agent-1",
            )
            self.assertEqual(
                builder.call_args.kwargs["assigned_adapter"],
                "local",
            )
            await runtime.stop()

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

    async def test_stop_is_idempotent_and_closed_runtime_cannot_restart(self) -> None:
        worker = FakeWorker()
        closeable = CloseRecorder()
        runtime = RunnerServiceRuntime(
            worker=worker,
            shutdown_timeout_seconds=1,
            closeables=(closeable,),
        )

        await runtime.start()
        await runtime.stop()
        await runtime.stop()

        self.assertEqual(worker.stop_requests, 1)
        self.assertTrue(closeable.closed)
        with self.assertRaisesRegex(RuntimeError, "has been stopped"):
            await runtime.start()


class RunnerServiceEndpointTests(unittest.TestCase):
    def test_health_and_readiness_expose_only_operational_state(self) -> None:
        worker = FakeWorker()
        worker._snapshot = replace(
            worker.snapshot,
            capacity_saturated_polls=2,
            last_claim_status=WorkspaceClaimStatus.CAPACITY_SATURATED,
        )
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
            self.assertEqual(
                ready.json()["worker"]["lastClaimStatus"],
                "capacity_saturated",
            )
            self.assertEqual(
                ready.json()["worker"]["capacitySaturatedPolls"],
                2,
            )
            rendered = ready.text
            self.assertNotIn("workspace-1", rendered)
            self.assertNotIn("agent-1", rendered)
            self.assertNotIn("control.example", rendered)

        self.assertEqual(worker.stop_requests, 1)


if __name__ == "__main__":
    unittest.main()
