"""Real child process: SQLite Mission Control, Runner, Harness, receipts and CAS."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from app.core.config import ArtifactStoreSettings
from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain import (
    ActorRef,
    ArtifactKind,
    ArtifactRef,
    ArtifactRetention,
    ArtifactSensitivity,
    ExecutionCheckpointPhase,
)
from app.repositories import MissionRepository
from app.services.artifact_store_service import ContentAddressedArtifactPublisher
from app.services.harness_checkpoint import HarnessEventType
from app.services.harness_service import FunctionTool
from app.services.mission_service import MissionService
from app.services.model_contract import ModelResponse, ModelUsage, ToolCall
from app.services.runner.model import DesktopTaskHarnessFactory
from app.services.runner_checkpoint import MissionControlHarnessCheckpointFactory
from app.services.runner_service import DesktopTaskClaimedWorkResolver, WorkUnitRunner
from app.services.tools.policy import ToolExecutionPolicy
from app.services.workspace_admission_service import WorkspaceClaimAdmissionPolicy


@asynccontextmanager
async def control_repository(path: Path):
    pool = SQLitePool(path)
    await pool.initialize()
    try:
        async with pool.acquire() as connection:
            await _create_mission_control_plane_sqlite(connection)

            @asynccontextmanager
            async def transaction():
                async with connection.transaction():
                    yield connection

            yield MissionRepository(execute=connection.execute, fetch_one=connection.fetchrow,
                fetch_all=connection.fetch, transaction_factory=transaction)
    finally:
        await pool.close()


class RealControl:
    def __init__(self, repository, root: Path, mode: str):
        self.repository = repository
        self.service = MissionService(repository)
        self.actor = ActorRef(type="runner", id="runner-1")
        self.root = root
        self.mode = mode

    async def claim_ready_work_unit(self, workspace_id, *, supported_capabilities=(), **kwargs):
        outcome = await self.service.claim_workspace_bound_work_unit(workspace_id,
            actor=self.actor, admission_policy=WorkspaceClaimAdmissionPolicy("workspace-1", 0), **kwargs)
        return {"claimStatus": outcome.status.value,
                "workUnit": outcome.work_unit.model_dump(mode="json", by_alias=True) if outcome.work_unit else None}

    async def record_execution_checkpoint(self, mission_id, work_unit_id, **kwargs):
        kwargs["phase"] = ExecutionCheckpointPhase(kwargs["phase"])
        result = await self.service.record_execution_checkpoint(mission_id, work_unit_id,
            actor=self.actor, **kwargs)
        if self.mode in {"terminal", "model"} and result.phase.value == {
            "terminal": HarnessEventType.EXECUTION_COMPLETED.value,
            "model": HarnessEventType.MODEL_STARTED.value,
        }[self.mode]:
            (self.root / "paused").write_text(self.mode)
            await asyncio.Event().wait()
        if (self.mode == "iteration" and result.phase == ExecutionCheckpointPhase.ITERATION_STARTED) or (
            self.mode == "resume_start" and result.phase == ExecutionCheckpointPhase.EXECUTION_STARTED and result.sequence > 1
        ):
            (self.root / "paused").write_text(self.mode)
            await asyncio.Event().wait()
        if self.mode in {"checkpoint", "short_deadline"} and result.phase == ExecutionCheckpointPhase.TOOL_COMPLETED and result.tool_calls == 1:
            (self.root / "paused").write_text("checkpoint")
            await asyncio.Event().wait()
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def get_execution_context(self, mission_id, work_unit_id, **kwargs):
        context = await self.service.get_claimed_execution_context(mission_id, work_unit_id, **kwargs)
        latest = await self.repository.get_latest_execution_checkpoint(work_unit_id, context.work_unit.attempt)
        projection = {"version": 1, "mission": context.mission.model_dump(mode="json", by_alias=True),
                      "workUnit": context.work_unit.model_dump(mode="json", by_alias=True),
                      "contract": context.contract.model_dump(mode="json", by_alias=True)}
        if latest is not None:
            projection["checkpoint"] = latest.model_dump(mode="json", by_alias=True, exclude_none=True)
        return {"executionContext": projection}

    async def heartbeat_work_unit(self, mission_id, work_unit_id, **kwargs):
        unit = await self.service.heartbeat_work_unit(mission_id, work_unit_id, actor=self.actor, **kwargs)
        return unit.model_dump(mode="json", by_alias=True)

    async def register_artifact(self, mission_id, work_unit_id, *, artifact, kind, **kwargs):
        value = await self.service.register_artifact(mission_id, work_unit_id, kind=ArtifactKind(kind),
            digest=artifact.digest, content_address=artifact.content_address, size_bytes=artifact.size_bytes,
            source_repository=None, base_commit=None, retention=ArtifactRetention.STANDARD,
            sensitivity=ArtifactSensitivity.INTERNAL, actor=self.actor, **kwargs)
        return value.model_dump(mode="json", by_alias=True)

    async def complete_work_unit(self, mission_id, work_unit_id, *, artifact_refs, **kwargs):
        value = await self.service.complete_work_unit(mission_id, work_unit_id,
            artifact_refs=[ArtifactRef(**ref) for ref in artifact_refs], actor=self.actor, **kwargs)
        return value.model_dump(mode="json", by_alias=True)

    async def fail_work_unit(self, mission_id, work_unit_id, **kwargs):
        value = await self.service.fail_work_unit(mission_id, work_unit_id, actor=self.actor, **kwargs)
        return value.model_dump(mode="json", by_alias=True)


class ProcessModel:
    def __init__(self, root: Path):
        self.root = root

    async def complete(self, request, tool_results):
        with (self.root / "model-calls").open("a") as handle:
            handle.write(str(len(tool_results)) + "\n")
        if not tool_results:
            return ModelResponse(tool_calls=(ToolCall("first", "append", {"text": "one"}),
                ToolCall("second", "append", {"text": "two"})), usage=ModelUsage(100, 20, .1))
        assert [result.content for result in tool_results] == ["written one", "written two"]
        return ModelResponse(content="real tools completed", usage=ModelUsage(50, 10, .05))


class ProcessModelFactory:
    recovery_manifest = {"provider": "deterministic-process-test", "model": "bounded-v1",
                         "messages": [{"role": "system", "content": "process acceptance"}]}

    def __init__(self, root: Path):
        self.root = root

    def build(self, tools):
        return ProcessModel(self.root)


async def execute(root: Path, mode: str):
    workspace = root / "workspace"
    async with control_repository(root / "control.sqlite3") as repository:
        control = RealControl(repository, root, mode)
        unit = await repository.get_work_unit("wu-1")

        async def append(arguments):
            with (workspace / "output").open("a") as handle:
                handle.write(arguments["text"] + "\n")
                handle.flush()
            if mode == "ambiguous":
                (root / "paused").write_text("started")
                await asyncio.Event().wait()
            return "written " + arguments["text"]

        model_factory = ProcessModelFactory(root)
        if mode == "context_changed":
            model_factory.recovery_manifest = {**model_factory.recovery_manifest, "model": "different"}
        factory = DesktopTaskHarnessFactory(model_factory,
            tools=[FunctionTool("append", append, lambda arguments: arguments)],
            workspace_root=workspace, recovery_state_root=root / "private",
            checkpoint_factory=MissionControlHarnessCheckpointFactory(control, runner_id="runner-1"),
            tool_policy=ToolExecutionPolicy.for_mode("edit", workspace), max_iterations=3,
            max_tool_calls=2, max_total_tokens=180)
        resolver = DesktopTaskClaimedWorkResolver(control, runner_id="runner-1", harness_factory=factory,
            max_timeout_seconds={"timeout_changed": 301, "short_deadline": 2, "resume_short": 2}.get(mode, 300))
        runner = WorkUnitRunner(control, runner_id="runner-1", assigned_agent_id="agent-1",
            assigned_adapter="function-calling", claimed_work_resolver=resolver,
            supported_work_unit_kinds=("desktop.task",), publisher=ContentAddressedArtifactPublisher(
                ArtifactStoreSettings(backend="local", local_root=root / "artifacts")))
        if mode in {"resume", "resume_short"}:
            poll = await runner.claim_ready_and_run("workspace-1")
            result = poll.run_result
            (root / "result.json").write_text(json.dumps({"claimStatus": poll.claim_status.value,
                "success": result.success if result else False, "status": result.work_unit["status"] if result else None}))
            return
        execution = await resolver.resolve(unit.model_dump(mode="json", by_alias=True))
        harness = execution.harness
        if mode == "receipt":
            complete = harness._receipt_store.complete

            def complete_then_pause(*args, **kwargs):
                complete(*args, **kwargs)
                (root / "paused").write_text("succeeded receipt")
                time.sleep(120)

            harness._receipt_store.complete = complete_then_pause
        result = await runner._run_leased("mis-1", "wu-1", unit.model_dump(mode="json", by_alias=True),
            code=execution.execution_input.code, language="text", timeout=execution.execution_input.timeout,
            cwd=workspace, lease_seconds=300, artifact_kind="test-result", media_type="text/plain",
            harness=harness, resume=execution.execution_input.resume)
        (root / "result.json").write_text(json.dumps({"success": result.success,
            "status": result.work_unit["status"]}))


if __name__ == "__main__":
    asyncio.run(execute(Path(sys.argv[1]), sys.argv[2]))
