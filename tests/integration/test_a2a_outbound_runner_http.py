from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request

from app.api.v1.a2a_adapter import (
    get_a2a_binding_selector,
    get_a2a_repository,
)
from app.api.v1.a2a_adapter import (
    router as a2a_router,
)
from app.api.v1.missions import (
    get_mission_repository,
    get_runner_workspace_grant_authorizer,
    get_workspace_claim_admission_policy_resolver,
)
from app.api.v1.missions import (
    router as missions_router,
)
from app.core.config import ArtifactStoreSettings
from app.services.a2a_outbound_composition import build_a2a_outbound_attempt_runner
from app.services.a2a_outbound_supervisor import A2AOutboundSupervisionOutcome
from app.services.a2a_outbound_worker import A2AOutboundWorkspaceRunner
from app.services.a2a_peer_credentials import OriginBoundA2ABearerProvider
from app.services.agent_binding_service import AgentBinding, StaticAgentBindingSelector
from app.services.artifact_store_service import ContentAddressedArtifactPublisher
from app.services.auth_service import get_current_user
from app.services.runner_service import MissionControlRunnerClient
from app.services.workspace_admission_service import WorkspaceClaimStatus
from tests.api.test_missions_api import (
    FakeMissionRepository,
    FakeRunnerWorkspaceGrantAuthorizer,
    FakeWorkspaceClaimAdmissionPolicyResolver,
)
from tests.services.test_a2a_outbound_composition import (
    signed_capable_card,
    strict_policy,
)
from tests.services.test_a2a_outbound_result import valid_result_payload
from tests.services.test_a2a_peer_route_service import json_response

_RECEIVER_ORIGIN = "https://receiver.example.test"
_RECEIVER_TOKEN = "receiver-issued-token"
_RUNNER_ID = "runner-a"
_WORKSPACE_ID = "workspace-1"


class _AtomicMissionRepository(FakeMissionRepository):
    """Serialize fake transactions without claiming database lock coverage."""

    def __init__(self) -> None:
        super().__init__()
        self._transaction_lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        async with self._transaction_lock:
            self.transaction_depth += 1
            try:
                yield self
            finally:
                self.transaction_depth -= 1


async def _authenticated_identity(request: Request) -> dict[str, str]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    if token == _WORKSPACE_ID:
        return {"id": token, "name": "Integration developer", "role": "developer"}
    if token == _RUNNER_ID:
        return {"id": token, "name": "Integration runner", "role": "runner"}
    raise HTTPException(status_code=401, detail="Unknown integration identity")


def _build_app(repository: _AtomicMissionRepository) -> FastAPI:
    application = FastAPI()
    application.include_router(a2a_router, prefix="/api/v1")
    application.include_router(missions_router, prefix="/api/v1")
    application.dependency_overrides[get_a2a_repository] = lambda: repository
    application.dependency_overrides[get_mission_repository] = lambda: repository
    application.dependency_overrides[get_a2a_binding_selector] = lambda: (
        StaticAgentBindingSelector(
            {
                _WORKSPACE_ID: [
                    AgentBinding(
                        agent_id="outbound-dispatcher",
                        adapter_type="a2a.outbound",
                        capabilities=("a2a.send",),
                    )
                ]
            }
        )
    )
    application.dependency_overrides[get_runner_workspace_grant_authorizer] = lambda: (
        FakeRunnerWorkspaceGrantAuthorizer({(_WORKSPACE_ID, _RUNNER_ID)})
    )
    admission_resolver = FakeWorkspaceClaimAdmissionPolicyResolver()
    application.dependency_overrides[get_workspace_claim_admission_policy_resolver] = (
        lambda: admission_resolver
    )
    application.dependency_overrides[get_current_user] = _authenticated_identity
    return application


class A2AOutboundRunnerHttpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_submission_runs_to_local_verification_through_http(self) -> None:
        repository = _AtomicMissionRepository()
        remote_requests: list[httpx.Request] = []
        remote_result_bytes = b"verified remote result"

        def remote_handler(request: httpx.Request) -> httpx.Response:
            remote_requests.append(request)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    content=json_response(signed_capable_card()),
                )

            envelope = json.loads(request.content)
            if envelope["method"] == "tasks/send":
                result = {"id": "external-task-1", "status": "completed"}
            else:
                result = valid_result_payload(content=remote_result_bytes)
                result["id"] = "external-task-1"
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "result": result, "id": envelope["id"]},
            )

        mission_transport = httpx.ASGITransport(app=_build_app(repository))
        remote_transport = httpx.MockTransport(remote_handler)
        with tempfile.TemporaryDirectory() as artifact_directory:
            async with (
                httpx.AsyncClient(
                    transport=mission_transport,
                    base_url="http://mission-control.test",
                ) as mission_http,
                httpx.AsyncClient(transport=remote_transport) as remote_http,
            ):
                submitted = await mission_http.post(
                    "/api/v1/a2a/tasks",
                    headers={"Authorization": f"Bearer {_WORKSPACE_ID}"},
                    json={
                        "taskId": "external-task-1",
                        "workspaceId": _WORKSPACE_ID,
                        "objective": "Delegate a durable task",
                        "agentUrl": f"{_RECEIVER_ORIGIN}/a2a",
                        "requiredCapabilities": ["artifact.write"],
                    },
                )
                self.assertEqual(submitted.status_code, 200, submitted.text)
                self.assertEqual(
                    {
                        "state": submitted.json()["state"],
                        "missionStatus": submitted.json()["missionStatus"],
                        "workUnitStatus": submitted.json()["workUnitStatus"],
                    },
                    {
                        "state": "submitted",
                        "missionStatus": "RUNNING",
                        "workUnitStatus": "PENDING",
                    },
                )

                control = MissionControlRunnerClient(
                    "http://mission-control.test",
                    access_token=_RUNNER_ID,
                    http_client=mission_http,
                )
                publisher = ContentAddressedArtifactPublisher(
                    ArtifactStoreSettings(
                        backend="local",
                        local_root=Path(artifact_directory),
                        publish_max_bytes=1024 * 1024,
                    )
                )
                attempt_runner = build_a2a_outbound_attempt_runner(
                    control,
                    publisher=publisher,
                    http_client=remote_http,
                    trust_policy=strict_policy(),
                    credential_provider=OriginBoundA2ABearerProvider(
                        {_RECEIVER_ORIGIN: _RECEIVER_TOKEN}
                    ),
                    runner_id=_RUNNER_ID,
                    source_agent_url="https://sender.example.test",
                    poll_interval_seconds=0.001,
                )
                runner = A2AOutboundWorkspaceRunner(
                    control,
                    attempt_runner,
                    runner_id=_RUNNER_ID,
                    assigned_agent_id="outbound-dispatcher",
                )

                first_poll = await runner.claim_ready_and_run(_WORKSPACE_ID)
                second_poll = await runner.claim_ready_and_run(_WORKSPACE_ID)

            self.assertEqual(first_poll.claim_status, WorkspaceClaimStatus.CLAIMED)
            self.assertIsNotNone(first_poll.supervision_result)
            self.assertEqual(
                first_poll.supervision_result.outcome,
                A2AOutboundSupervisionOutcome.LOCAL_VERIFYING,
            )
            self.assertEqual(second_poll.claim_status, WorkspaceClaimStatus.IDLE)
            self.assertIsNone(second_poll.supervision_result)

            work_unit = repository.work_units[0]
            self.assertEqual(work_unit.status.value, "VERIFYING")
            self.assertEqual(work_unit.attempt, 1)
            self.assertIsNone(work_unit.lease)
            self.assertEqual(len(repository.artifacts), 2)
            self.assertEqual(repository.evidence, [])

            artifact_contents: dict[str, bytes] = {}
            for artifact in repository.artifacts:
                digest_hex = artifact.digest.removeprefix("sha256:")
                stored_path = Path(artifact_directory) / "sha256" / digest_hex
                self.assertTrue(stored_path.is_file())
                content = stored_path.read_bytes()
                self.assertEqual(len(content), artifact.size_bytes)
                self.assertEqual(hashlib.sha256(content).hexdigest(), digest_hex)
                self.assertEqual(
                    artifact.content_address,
                    f"local:sha256/{digest_hex}",
                )
                artifact_contents[artifact.media_type] = content
            self.assertEqual(
                artifact_contents["application/json"],
                remote_result_bytes,
            )
            attestation = json.loads(
                artifact_contents["application/vnd.agenthub.a2a-attestation+json"]
            )
            self.assertEqual(attestation["remoteTaskId"], "external-task-1")
            self.assertEqual(attestation["evidence"][0]["verdict"], "PASS")

        lifecycle_types = [event.event_type for event in repository.events]
        for expected in (
            "work_unit.lifecycle.leased",
            "work_unit.lifecycle.started",
            "work_unit.lifecycle.completed",
        ):
            self.assertEqual(lifecycle_types.count(expected), 1)
        self.assertEqual(lifecycle_types.count("artifact.lifecycle.registered"), 2)
        execution_types = {
            "work_unit.lifecycle.started",
            "artifact.lifecycle.registered",
            "work_unit.lifecycle.completed",
        }
        self.assertTrue(
            all(
                event.actor.type.value == "runner"
                for event in repository.events
                if event.event_type in execution_types
            )
        )

        card_requests = [
            request for request in remote_requests if request.method == "GET"
        ]
        task_requests = [
            request for request in remote_requests if request.method == "POST"
        ]
        self.assertTrue(card_requests)
        self.assertTrue(
            all(
                request.headers.get("Authorization") is None
                for request in card_requests
            )
        )
        self.assertTrue(
            all(
                request.headers.get("Authorization") == f"Bearer {_RECEIVER_TOKEN}"
                for request in task_requests
            )
        )
        methods = [json.loads(request.content)["method"] for request in task_requests]
        self.assertEqual(methods.count("tasks/send"), 1)
        self.assertEqual(methods.count("tasks/get"), 1)


if __name__ == "__main__":
    unittest.main()
