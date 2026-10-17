"""Test-only driver using real Mission commands and a deterministic ModelPort."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain import ActorRef, ArtifactRef
from app.repositories import MissionRepository, SessionEventRepository
from app.services.artifact_store_service import ContentAddressedArtifactPublisher
from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.harness_service import FunctionCallingHarness
from app.services.harness_types import HarnessRequest
from app.services.mission_service import MissionService
from app.services.model_contract import ModelResponse, ModelStreamEvent
from app.services.workspace_admission_service import WorkspaceClaimAdmissionPolicy

WORKSPACE_ID = "workspace-chat-browser"
AGENT_ID = "local-desktop-agent"
FIXTURE_TEXT = "固定测试输出：产物 中文。仅验证聊天传输，不代表任务验收通过。"
RUNNER = ActorRef(type="runner", id="chat-browser-runner")


class FixedFixtureModel:
    async def complete(self, request):
        return ModelResponse(text=FIXTURE_TEXT)

    async def stream(self, request):
        yield ModelStreamEvent(kind="text_delta", text=FIXTURE_TEXT[:8])
        yield ModelStreamEvent(kind="text_delta", text=FIXTURE_TEXT[8:])
        yield ModelStreamEvent(kind="completed")


class MissionChatDriver:
    def __init__(self, store):
        self.repository = MissionRepository()
        self.service = MissionService(self.repository, session_event_repository=SessionEventRepository())
        self.store = store
        self.outputs: dict[tuple[str, int], str] = {}
        self.lock = asyncio.Lock()

    async def snapshot(self, mission_id: str) -> dict:
        mission = await self.repository.get_mission(mission_id)
        if mission is None or mission.workspace_id != WORKSPACE_ID:
            raise ValueError("Fixture Mission not found")
        events = await self.repository.list_events(mission_id)
        events += await self.repository.list_work_unit_events(mission_id)
        events.sort(key=lambda event: (event.occurred_at, event.event_id))
        return {
            "mission": mission.to_public_dict(),
            "workUnits": [item.to_public_dict() for item in await self.repository.list_work_units(mission_id)],
            "artifacts": [item.to_public_dict() for item in await self.repository.list_artifacts(mission_id)],
            "evidence": [item.to_public_dict() for item in await self.repository.list_evidence(mission_id)],
            "decisions": [item.to_public_dict() for item in await self.repository.list_decisions(mission_id)],
            "events": [item.to_public_dict() for item in events],
        }

    async def _unit(self, mission_id):
        await self.snapshot(mission_id)
        units = await self.repository.list_work_units(mission_id)
        if len(units) != 1 or units[0].assigned_agent_id != AGENT_ID:
            raise ValueError("Fixture requires one admitted catalog-bound WorkUnit")
        return units[0]

    async def _start(self, mission_id, lease_seconds):
        unit = await self._unit(mission_id)
        if unit.status.value != "PENDING":
            raise ValueError("Fixture start requires a PENDING WorkUnit")
        # Guard the fixture driver from accidentally claiming a different test's
        # ready work. The production claim still applies its actual selection.
        for mission in await self.repository.list_missions(WORKSPACE_ID):
            if mission.id == mission_id or mission.status.value != "RUNNING":
                continue
            others = await self.repository.list_work_units(mission.id)
            if any(other.status.value in {"PENDING", "RETRYING"} for other in others):
                raise ValueError("Cancel other pending fixture Missions before advancing")
        outcome = await self.service.claim_workspace_bound_work_unit(
            WORKSPACE_ID, agent_id=AGENT_ID, adapter_type="function-calling",
            supported_work_unit_kinds=("desktop.task",), runner_id=RUNNER.id,
            actor=RUNNER, lease_seconds=lease_seconds,
            admission_policy=WorkspaceClaimAdmissionPolicy(tenant_id=WORKSPACE_ID, max_concurrent=0),
        )
        if outcome.work_unit is None or outcome.work_unit.id != unit.id:
            raise ValueError("Fixture production claim did not select the requested WorkUnit")
        unit = outcome.work_unit
        return await self.service.start_work_unit(
            mission_id, unit.id, lease_id=unit.lease.id, runner_id=RUNNER.id, actor=RUNNER,
        )

    async def _text(self, mission_id):
        unit = await self._unit(mission_id)
        if unit.status.value != "RUNNING" or unit.lease is None:
            raise ValueError("Fixture text requires a RUNNING leased WorkUnit")
        if unit.lease.expires_at <= datetime.now(UTC):
            raise ValueError("Fixture text cannot use an expired lease")
        key = (mission_id, unit.attempt)
        if key in self.outputs:
            return self.outputs[key]

        async def publish(text, event_type="harness.assistant.delta"):
            await self.service.publish_streaming_event(
                mission_id, unit.id, event_id="evt-fixture-" + uuid.uuid4().hex,
                event_type=event_type, text=text, attempt=unit.attempt,
                lease_id=unit.lease.id, runner_id=RUNNER.id, actor=RUNNER,
            )

        result = await FunctionCallingHarness(
            FixedFixtureModel(), [], max_iterations=1, model_retries=0,
        ).execute(HarnessRequest(
            "Return the fixed fixture text", "text", 5,
            execution=HarnessExecutionContext(mission_id, unit.id, unit.attempt),
            on_text_delta=publish,
        ))
        if not result.sandbox.success or result.sandbox.stdout != FIXTURE_TEXT:
            raise ValueError("Fixture Harness did not produce its fixed test output")
        await publish(result.sandbox.stdout, "harness.assistant.completed")
        self.outputs[key] = result.sandbox.stdout
        return result.sandbox.stdout

    async def _complete(self, mission_id, *, decision):
        unit = await self._unit(mission_id)
        if unit.status.value == "PENDING":
            unit = await self._start(mission_id, 60)
        text = await self._text(mission_id)
        published = await ContentAddressedArtifactPublisher(self.store).publish_bytes(text.encode("utf-8"))
        arguments = {"lease_id": unit.lease.id, "runner_id": RUNNER.id, "actor": RUNNER}
        artifact = await self.service.register_artifact(
            mission_id, unit.id, artifact_id="artifact-chat-" + uuid.uuid4().hex,
            kind="report" if decision else "test-result", digest=published.digest,
            content_address=published.content_address, media_type="text/plain",
            size_bytes=published.size_bytes, source_repository=None, base_commit=None,
            retention="ephemeral", sensitivity="internal", **arguments,
        )
        await self.service.complete_work_unit(
            mission_id, unit.id, artifact_refs=[ArtifactRef(id=artifact.id, digest=artifact.digest)],
            **arguments,
        )
        if decision:
            # The default chat Contract requires test-result. Publishing only
            # report deliberately lets the real policy request governance.
            outcome = await self.service.discover_workspace_verification_work(WORKSPACE_ID)
            if outcome.context is None or outcome.context.mission.id != mission_id:
                raise ValueError("Fixture verification discovery selected another Mission")

    async def advance(self, mission_id, action, lease_seconds):
        async with self.lock:
            if action == "start":
                await self._start(mission_id, lease_seconds)
            elif action == "text":
                await self._text(mission_id)
            elif action == "expire":
                unit = await self._unit(mission_id)
                if unit.status.value == "PENDING":
                    unit = await self._start(mission_id, 1)
                if unit.lease is None:
                    raise ValueError("Fixture expiry requires a real lease")
                remaining = (unit.lease.expires_at - datetime.now(UTC)).total_seconds()
                if remaining > 2:
                    raise ValueError("Use a one-second fixture lease to observe expiry")
                await asyncio.sleep(max(0, remaining) + 0.05)
            else:
                await self._complete(mission_id, decision=action == "decision")
            return await self.snapshot(mission_id)
