from __future__ import annotations

import asyncio
import hashlib
import unittest
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from app.api.v1.missions import (
    get_mission_repository,
    get_runner_workspace_grant_authorizer,
    router,
)
from app.domain import Mission, WorkUnit
from app.services.artifact_store_service import PublishedArtifact
from app.services.auth_service import get_current_user
from app.services.harness_service import HarnessRequest, HarnessResult
from app.services.runner_service import (
    ClaimedWorkExecution,
    MissionControlRunnerClient,
    RunnerExecutionInput,
    WorkUnitRunner,
)
from app.services.tools.sandbox_executor import SandboxResult
from tests.api.test_missions_api import (
    FakeMissionRepository,
    FakeRunnerWorkspaceGrantAuthorizer,
)
from tests.domain.factories import build_mission, build_work_unit


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


async def _authenticated_runner(request: Request) -> dict[str, str]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"id": token, "name": token, "role": "runner"}


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
    application.dependency_overrides[get_current_user] = _authenticated_runner
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

        self.assertTrue(all(result is not None for result in results))
        self.assertIsNone(empty)
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

        self.assertEqual(sum(result is not None for result in results), 1)
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
            ),
            resolver,
        )


if __name__ == "__main__":
    unittest.main()
