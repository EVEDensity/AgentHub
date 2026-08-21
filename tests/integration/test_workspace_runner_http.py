from __future__ import annotations

import asyncio
import hashlib
import json
import unittest
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from starlette.responses import JSONResponse

from app.api.v1.missions import (
    get_mission_repository,
    get_runner_workspace_grant_authorizer,
    get_workspace_claim_admission_policy_resolver,
    router,
)
from app.domain import Lease, Mission, WorkUnit
from app.services.artifact_store_service import PublishedArtifact
from app.services.auth_service import get_current_user
from app.services.capability_tools import CapabilityToolBinding
from app.services.harness_checkpoint import (
    HarnessCheckpoint,
    HarnessEvent,
    HarnessEventType,
    HarnessExecutionContext,
)
from app.services.harness_service import (
    FunctionResult,
    FunctionTool,
    HarnessRequest,
    HarnessResult,
    ModelResponse,
    ModelUsage,
)
from app.services.runner_checkpoint import MissionControlHarnessCheckpointPort
from app.services.runner_composition import (
    build_a2a_inbound_runner,
    build_kind_aware_workspace_runner,
)
from app.services.runner_service import (
    ClaimedWorkExecution,
    MissionControlRunnerClient,
    RunnerExecutionError,
    RunnerExecutionInput,
    WorkUnitRunner,
)
from app.services.tools.sandbox_executor import SandboxResult
from app.services.workspace_admission_service import WorkspaceClaimStatus
from tests.api.test_missions_api import (
    FakeMissionRepository,
    FakeRunnerWorkspaceGrantAuthorizer,
    FakeWorkspaceClaimAdmissionPolicyResolver,
)
from tests.domain.factories import build_contract, build_mission, build_work_unit


class _AtomicMissionRepository(FakeMissionRepository):
    """Serialize fake transactions without pretending to model PostgreSQL locks."""

    def __init__(self, missions: list[Mission], work_units: list[WorkUnit]) -> None:
        super().__init__()
        self.list_result = list(missions)
        self.work_units = list(work_units)
        self._transaction_lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        async with self._transaction_lock:
            self.transaction_depth += 1
            try:
                yield self
            finally:
                self.transaction_depth -= 1

    async def get_mission(self, mission_id: str) -> Mission | None:
        return next(
            (mission for mission in self.list_result if mission.id == mission_id),
            None,
        )

    async def update_mission(self, mission: Mission) -> None:
        for index, existing in enumerate(self.list_result):
            if existing.id == mission.id:
                self.list_result[index] = mission
                return
        self.list_result.append(mission)


class _SuccessfulHarness:
    async def execute(self, request: HarnessRequest) -> HarnessResult:
        return HarnessResult(
            sandbox=SandboxResult(
                success=True,
                stdout=f"completed {request.execution.work_unit_id}\n",
                stderr="",
                exit_code=0,
                duration_ms=1,
                mode="integration",
            )
        )


class _StaticResolver:
    def __init__(self) -> None:
        self.claimed_ids: list[str] = []

    async def resolve(
        self,
        work_unit: Mapping[str, Any],
    ) -> ClaimedWorkExecution:
        work_unit_id = work_unit.get("id")
        if not isinstance(work_unit_id, str):
            raise TypeError("claimed WorkUnit has no id")
        self.claimed_ids.append(work_unit_id)
        return ClaimedWorkExecution(
            execution_input=RunnerExecutionInput(
                code=f"execute {work_unit_id}",
                language="text",
            ),
            harness=_SuccessfulHarness(),
        )


class _FinalModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        request: HarnessRequest,
        tool_results: tuple[FunctionResult, ...],
    ) -> ModelResponse:
        del request, tool_results
        self.calls += 1
        return ModelResponse(content="checkpointed result")


class _FinalModelFactory:
    def __init__(self) -> None:
        self.model = _FinalModel()

    def build(self, tools: Sequence[FunctionTool]) -> _FinalModel:
        del tools
        return self.model


class _RoutingModel:
    def __init__(self) -> None:
        self.schemas: list[str] = []

    async def complete(
        self,
        request: HarnessRequest,
        tool_results: tuple[FunctionResult, ...],
    ) -> ModelResponse:
        del tool_results
        schema = json.loads(request.code)["schema"]
        self.schemas.append(schema)
        return ModelResponse(content=schema)


class _RoutingModelFactory:
    def __init__(self) -> None:
        self.model = _RoutingModel()

    def build(self, tools: Sequence[FunctionTool]) -> _RoutingModel:
        del tools
        return self.model


class _EmptyBindingFactory:
    def build(
        self,
        execution: HarnessExecutionContext,
    ) -> Sequence[CapabilityToolBinding]:
        del execution
        return ()


class _RecordingPublisher:
    def __init__(self) -> None:
        self.contents: list[bytes] = []

    async def publish_bytes(self, content: bytes) -> PublishedArtifact:
        self.contents.append(content)
        digest = hashlib.sha256(content).hexdigest()
        return PublishedArtifact(
            digest=f"sha256:{digest}",
            size_bytes=len(content),
            content_address=f"local:sha256/{digest}",
        )


async def _authenticated_actor(request: Request) -> dict[str, str]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    role = "developer" if token == "workspace-1" else "runner"
    return {"id": token, "name": token, "role": role}


def _build_app(repository: _AtomicMissionRepository) -> FastAPI:
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_mission_repository] = lambda: repository
    grant_authorizer = FakeRunnerWorkspaceGrantAuthorizer(
        {
            ("workspace-1", "runner-a"),
            ("workspace-1", "runner-b"),
        }
    )
    application.dependency_overrides[get_runner_workspace_grant_authorizer] = (
        lambda: grant_authorizer
    )
    admission_resolver = FakeWorkspaceClaimAdmissionPolicyResolver()
    application.dependency_overrides[get_workspace_claim_admission_policy_resolver] = (
        lambda: admission_resolver
    )
    application.dependency_overrides[get_current_user] = _authenticated_actor
    return application


def _mission(mission_id: str) -> Mission:
    return build_mission(
        id=mission_id,
        status="RUNNING",
        source={
            "type": "a2a.inbound",
            "reference": "https://peer.example.test",
            "externalId": f"remote-{mission_id}",
        },
    )


def _work_unit(mission_id: str, work_unit_id: str) -> WorkUnit:
    return build_work_unit(
        id=work_unit_id,
        mission_id=mission_id,
        kind="a2a.inbound",
        required_capabilities=["a2a.receive"],
        assigned_agent_id="reviewer",
        assigned_adapter="local_codex",
    )


class WorkspaceRunnerHttpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_kind_aware_runner_routes_inbound_and_fork_roots_over_http(
        self,
    ) -> None:
        missions = [
            _mission("mission-a-inbound"),
            build_mission(
                id="mission-b-fork",
                status="RUNNING",
                objective="Continue from verified source artifacts.",
                source={
                    "type": "mission.fork",
                    "reference": "mission-source",
                    "externalId": "checkpoint-source",
                },
            ),
        ]
        work_units = [
            _work_unit("mission-a-inbound", "work-inbound"),
            build_work_unit(
                id="work-fork",
                mission_id="mission-b-fork",
                kind="mission.fork",
                input_refs=[
                    {"id": "artifact-source", "digest": "sha256:" + "a" * 64}
                ],
                expected_outputs=[{"kind": "report", "required": True}],
                required_capabilities=[],
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            ),
        ]
        repository = _AtomicMissionRepository(missions, work_units)
        repository.contract = build_contract(
            allowed_capabilities=[{"capability": "a2a.receive", "scope": {}}]
        )
        model_factory = _RoutingModelFactory()
        publisher = _RecordingPublisher()
        transport = httpx.ASGITransport(app=_build_app(repository))

        async with httpx.AsyncClient(transport=transport) as http_client:
            control = MissionControlRunnerClient(
                "http://mission-control.test",
                access_token="runner-a",
                http_client=http_client,
            )
            runner = build_kind_aware_workspace_runner(
                control,
                publisher=publisher,
                model_factory=model_factory,
                binding_factory=_EmptyBindingFactory(),
                runner_id="runner-a",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            )
            results = [
                await runner.claim_ready_and_run("workspace-1"),
                await runner.claim_ready_and_run("workspace-1"),
            ]

        self.assertTrue(
            all(
                result.claim_status == WorkspaceClaimStatus.CLAIMED
                and result.run_result is not None
                and result.run_result.success
                for result in results
            )
        )
        self.assertEqual(
            model_factory.model.schemas,
            [
                "agenthub.a2a-inbound-context.v1",
                "agenthub.mission-fork-context.v1",
            ],
        )
        self.assertEqual(
            publisher.contents,
            [
                b"agenthub.a2a-inbound-context.v1",
                b"agenthub.mission-fork-context.v1",
            ],
        )
        self.assertEqual(
            {work_unit.status.value for work_unit in repository.work_units},
            {"VERIFYING"},
        )

    async def test_checkpoint_rejection_fails_before_model_and_recovers_work(
        self,
    ) -> None:
        mission = _mission("mission-rejected")
        work_unit = _work_unit("mission-rejected", "work-rejected")
        repository = _AtomicMissionRepository([mission], [work_unit])
        repository.mission = mission
        repository.contract = build_contract(
            allowed_capabilities=[{"capability": "a2a.receive", "scope": {}}]
        )
        application = _build_app(repository)

        @application.middleware("http")
        async def reject_checkpoint(request: Request, call_next: Any):
            if request.url.path.endswith("/checkpoints"):
                return JSONResponse(
                    status_code=409,
                    content={"detail": "checkpoint admission rejected"},
                )
            return await call_next(request)

        model_factory = _FinalModelFactory()
        publisher = _RecordingPublisher()
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport) as http_client:
            control = MissionControlRunnerClient(
                "http://mission-control.test",
                access_token="runner-a",
                http_client=http_client,
            )
            runner = build_a2a_inbound_runner(
                control,
                publisher=publisher,
                model_factory=model_factory,
                binding_factory=_EmptyBindingFactory(),
                runner_id="runner-a",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            )
            with self.assertRaisesRegex(
                RunnerExecutionError,
                "Harness execution failed",
            ):
                await runner.claim_ready_and_run("workspace-1")

        self.assertEqual(model_factory.model.calls, 0)
        self.assertEqual(publisher.contents, [])
        self.assertEqual(repository.execution_checkpoints, [])
        self.assertEqual(repository.work_units[0].status.value, "FAILED")
        failed_events = [
            event
            for event in repository.events
            if event.event_type == "work_unit.lifecycle.failed"
        ]
        self.assertEqual(len(failed_events), 1)
        self.assertNotIn("checkpoint admission rejected", str(failed_events[0].payload))

    async def test_production_inbound_runner_persists_checkpoints_over_http(
        self,
    ) -> None:
        mission = _mission("mission-checkpoint")
        work_unit = _work_unit("mission-checkpoint", "work-checkpoint")
        repository = _AtomicMissionRepository([mission], [work_unit])
        repository.mission = mission
        repository.contract = build_contract(
            allowed_capabilities=[{"capability": "a2a.receive", "scope": {}}]
        )
        transport = httpx.ASGITransport(app=_build_app(repository))

        async with httpx.AsyncClient(transport=transport) as http_client:
            control = MissionControlRunnerClient(
                "http://mission-control.test",
                access_token="runner-a",
                http_client=http_client,
            )
            runner = build_a2a_inbound_runner(
                control,
                publisher=_RecordingPublisher(),
                model_factory=_FinalModelFactory(),
                binding_factory=_EmptyBindingFactory(),
                runner_id="runner-a",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            )
            result = await runner.claim_ready_and_run("workspace-1")

        self.assertEqual(result.claim_status, WorkspaceClaimStatus.CLAIMED)
        self.assertIsNotNone(result.run_result)
        assert result.run_result is not None
        self.assertTrue(result.run_result.success)
        self.assertEqual(repository.work_units[0].status.value, "VERIFYING")
        self.assertEqual(
            [checkpoint.sequence for checkpoint in repository.execution_checkpoints],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [checkpoint.phase.value for checkpoint in repository.execution_checkpoints],
            [
                "harness.execution.started",
                "harness.iteration.started",
                "harness.model.started",
                "harness.model.completed",
                "harness.execution.completed",
            ],
        )
        checkpoint_events = [
            event
            for event in repository.events
            if event.event_type == "work_unit.checkpoint.recorded"
        ]
        self.assertEqual(len(checkpoint_events), 5)
        self.assertTrue(
            all("toolResults" not in event.payload for event in checkpoint_events)
        )
        self.assertTrue(
            all(
                event.payload["stateDigest"].startswith("sha256:")
                for event in checkpoint_events
            )
        )
        self.assertEqual(
            [event.payload["sequence"] for event in checkpoint_events],
            [1, 2, 3, 4, 5],
        )
        self.assertTrue(
            all(event.actor.id == "runner-a" for event in checkpoint_events)
        )

    async def test_expired_attempt_is_retried_with_distinct_checkpoint_ancestry(
        self,
    ) -> None:
        mission = _mission("mission-retry-checkpoint")
        work_unit = _work_unit("mission-retry-checkpoint", "work-retry-checkpoint")
        repository = _AtomicMissionRepository([mission], [work_unit])
        repository.mission = mission
        repository.contract = build_contract(
            allowed_capabilities=[{"capability": "a2a.receive", "scope": {}}]
        )
        transport = httpx.ASGITransport(app=_build_app(repository))

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://mission-control.test",
        ) as http_client:
            control = MissionControlRunnerClient(
                "http://mission-control.test",
                access_token="runner-a",
                http_client=http_client,
            )
            claimed = await control.claim_ready_work_unit(
                "workspace-1",
                runner_id="runner-a",
                agent_id="reviewer",
                adapter_type="local_codex",
                supported_work_unit_kinds=("a2a.inbound",),
                lease_seconds=300,
            )
            claimed_work_unit = claimed["workUnit"]
            attempt_one_lease_id = claimed_work_unit["lease"]["id"]
            await control.start_work_unit(
                mission.id,
                work_unit.id,
                runner_id="runner-a",
                lease_id=attempt_one_lease_id,
            )

            execution = HarnessExecutionContext(mission.id, work_unit.id, 1)
            checkpoint_port = MissionControlHarnessCheckpointPort(
                control,
                execution=execution,
                runner_id="runner-a",
                lease_id=attempt_one_lease_id,
            )
            usage = ModelUsage()
            await checkpoint_port.record(
                HarnessCheckpoint(
                    sequence=1,
                    phase=HarnessEventType.EXECUTION_STARTED,
                    execution=execution,
                    iteration=0,
                    tool_calls=0,
                    usage=usage,
                    tool_results=(),
                ),
                HarnessEvent(
                    sequence=1,
                    event_type=HarnessEventType.EXECUTION_STARTED,
                    execution=execution,
                    duration_ms=1,
                    iteration=0,
                    tool_calls=0,
                    usage=usage,
                ),
            )
            attempt_one_checkpoint_id = repository.execution_checkpoints[0].id

            running = repository.work_units[0]
            assert running.lease is not None
            repository.work_units[0] = running.model_copy(
                update={
                    "lease": Lease(
                        id=running.lease.id,
                        runner_id=running.lease.runner_id,
                        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                    )
                }
            )
            recovered = await http_client.post(
                (
                    f"/api/v1/missions/{mission.id}/work-units/"
                    f"{work_unit.id}/recover"
                ),
                headers={"Authorization": "Bearer workspace-1"},
            )
            self.assertEqual(recovered.status_code, 200)
            self.assertEqual(recovered.json()["status"], "RETRYING")
            self.assertEqual(recovered.json()["attempt"], 1)
            self.assertNotIn("lease", recovered.json())

            stale_checkpoint = await http_client.post(
                (
                    f"/api/v1/missions/{mission.id}/work-units/"
                    f"{work_unit.id}/checkpoints"
                ),
                headers={"Authorization": "Bearer runner-a"},
                json={
                    "id": "chk-stale-attempt-one",
                    "leaseId": attempt_one_lease_id,
                    "sequence": 2,
                    "phase": "harness.iteration.started",
                    "iteration": 1,
                    "toolCalls": 0,
                    "promptTokens": 0,
                    "completionTokens": 0,
                    "modelCost": 0,
                    "terminal": False,
                },
            )
            self.assertEqual(stale_checkpoint.status_code, 403)

            runner = build_a2a_inbound_runner(
                control,
                publisher=_RecordingPublisher(),
                model_factory=_FinalModelFactory(),
                binding_factory=_EmptyBindingFactory(),
                runner_id="runner-a",
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
            )
            result = await runner.claim_ready_and_run("workspace-1")

        self.assertEqual(result.claim_status, WorkspaceClaimStatus.CLAIMED)
        self.assertIsNotNone(result.run_result)
        assert result.run_result is not None
        self.assertTrue(result.run_result.success)
        self.assertEqual(repository.work_units[0].status.value, "VERIFYING")
        self.assertEqual(repository.work_units[0].attempt, 2)
        attempt_one = [
            checkpoint
            for checkpoint in repository.execution_checkpoints
            if checkpoint.attempt == 1
        ]
        attempt_two = [
            checkpoint
            for checkpoint in repository.execution_checkpoints
            if checkpoint.attempt == 2
        ]
        self.assertEqual([checkpoint.sequence for checkpoint in attempt_one], [1])
        self.assertEqual(
            [checkpoint.sequence for checkpoint in attempt_two],
            [1, 2, 3, 4, 5],
        )
        self.assertNotEqual(attempt_one_checkpoint_id, attempt_two[0].id)
        self.assertEqual(len(repository.execution_checkpoints), 6)

    async def test_concurrent_runners_execute_distinct_missions_then_observe_empty(self) -> None:
        repository = _AtomicMissionRepository(
            [_mission("mission-a"), _mission("mission-b")],
            [
                _work_unit("mission-a", "work-a"),
                _work_unit("mission-b", "work-b"),
            ],
        )
        transport = httpx.ASGITransport(app=_build_app(repository))
        async with (
            httpx.AsyncClient(transport=transport) as http_a,
            httpx.AsyncClient(transport=transport) as http_b,
        ):
            runner_a, resolver_a = self._runner(http_a, "runner-a")
            runner_b, resolver_b = self._runner(http_b, "runner-b")

            results = await asyncio.gather(
                runner_a.claim_ready_and_run("workspace-1"),
                runner_b.claim_ready_and_run("workspace-1"),
            )
            empty = await runner_a.claim_ready_and_run("workspace-1")

        self.assertTrue(
            all(
                result.claim_status == WorkspaceClaimStatus.CLAIMED
                and result.run_result is not None
                for result in results
            )
        )
        self.assertEqual(empty.claim_status, WorkspaceClaimStatus.IDLE)
        self.assertIsNone(empty.run_result)
        claimed_ids = resolver_a.claimed_ids + resolver_b.claimed_ids
        self.assertCountEqual(claimed_ids, ["work-a", "work-b"])
        self.assertEqual(len(claimed_ids), len(set(claimed_ids)))
        self.assertEqual(
            {unit.status.value for unit in repository.work_units},
            {"VERIFYING"},
        )
        leased_by = {
            event.payload["runnerId"]
            for event in repository.events
            if event.event_type == "work_unit.lifecycle.leased"
        }
        self.assertEqual(leased_by, {"runner-a", "runner-b"})
        execution_events = {
            "work_unit.lifecycle.started",
            "artifact.lifecycle.registered",
            "work_unit.lifecycle.completed",
        }
        self.assertTrue(
            all(
                event.actor.type.value == "runner"
                for event in repository.events
                if event.event_type in execution_events
            )
        )

    async def test_two_runners_cannot_execute_the_same_work_unit(self) -> None:
        repository = _AtomicMissionRepository(
            [_mission("mission-a")],
            [_work_unit("mission-a", "work-a")],
        )
        transport = httpx.ASGITransport(app=_build_app(repository))
        async with (
            httpx.AsyncClient(transport=transport) as http_a,
            httpx.AsyncClient(transport=transport) as http_b,
        ):
            runner_a, resolver_a = self._runner(http_a, "runner-a")
            runner_b, resolver_b = self._runner(http_b, "runner-b")

            results = await asyncio.gather(
                runner_a.claim_ready_and_run("workspace-1"),
                runner_b.claim_ready_and_run("workspace-1"),
            )

        self.assertEqual(
            sum(
                result.claim_status == WorkspaceClaimStatus.CLAIMED
                for result in results
            ),
            1,
        )
        claimed_ids = resolver_a.claimed_ids + resolver_b.claimed_ids
        self.assertEqual(claimed_ids, ["work-a"])
        self.assertEqual(repository.work_units[0].status.value, "VERIFYING")
        leased_events = [
            event
            for event in repository.events
            if event.event_type == "work_unit.lifecycle.leased"
        ]
        self.assertEqual(len(leased_events), 1)

    @staticmethod
    def _runner(
        http_client: httpx.AsyncClient,
        runner_id: str,
    ) -> tuple[WorkUnitRunner, _StaticResolver]:
        resolver = _StaticResolver()
        control = MissionControlRunnerClient(
            "http://mission-control.test",
            access_token=runner_id,
            http_client=http_client,
        )
        return (
            WorkUnitRunner(
                control,
                publisher=_RecordingPublisher(),
                runner_id=runner_id,
                assigned_agent_id="reviewer",
                assigned_adapter="local_codex",
                claimed_work_resolver=resolver,
                supported_work_unit_kinds=("a2a.inbound",),
            ),
            resolver,
        )


if __name__ == "__main__":
    unittest.main()
