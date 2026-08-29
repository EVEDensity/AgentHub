from __future__ import annotations

import asyncio
import copy
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.config import WORKSPACES_DIR
from app.domain import (
    AcceptanceCriterion,
    ActorRef,
    ActorType,
    Budgets,
    CriterionKind,
    Lease,
    Mission,
    MissionContract,
    MissionSource,
    MissionSourceType,
    MissionStatus,
    OutputSpec,
    WorkUnit,
    WorkUnitStatus,
)
from app.services.artifact_store_service import PublishedArtifact
from app.services.desktop_local_runner import (
    DESKTOP_ADAPTER_TYPE,
    DESKTOP_AGENT_ID,
    ENABLE_ENV,
    DesktopLocalRunnerController,
    DesktopLocalRunnerSettings,
    derive_desktop_task_work_units,
    shutdown_desktop_local_runner,
    startup_desktop_local_runner,
)
from app.services.desktop_runner_tools import (
    DESKTOP_TOOL_RESULT_MAX_CHARS,
    build_desktop_runner_tools,
)
from app.services.harness_service import (
    FunctionCall,
    ModelResponse,
    ModelUsage,
)

RUNNER_USER_ID = "user-1"
WORKSPACE_ID = "local-admin"


def desktop_settings(**overrides: Any) -> DesktopLocalRunnerSettings:
    base: dict[str, Any] = {
        "enabled": True,
        "base_url": "http://127.0.0.1:28000",
        "admin_name": "admin",
        "admin_password": "admin123",
        "token": None,
        "token_file": None,
        "user_id": RUNNER_USER_ID,
        "workspace_id": WORKSPACE_ID,
        "workspace_root": None,
        "model_name": None,
        "max_iterations": 8,
        "max_tool_calls": 32,
        "max_total_tokens": 200_000,
        "timeout_seconds": 300.0,
        "lease_seconds": 300,
        "idle_delay_seconds": 0.01,
        "max_delay_seconds": 0.05,
        "derivation_interval_seconds": 0.01,
    }
    base.update(overrides)
    return DesktopLocalRunnerSettings(**base)


async def call_tool(workspace_root: Path, name: str, arguments: dict[str, Any]) -> str:
    tools = {tool.name: tool for tool in build_desktop_runner_tools(workspace_root)}
    return await tools[name].handler(arguments)


class DesktopWorkspaceTestCase(unittest.IsolatedAsyncioTestCase):
    """Runs each test against an isolated temporary workspace."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace_root = Path(self._tmp.name) / "workspace"
        self.workspace_root.mkdir(parents=True, exist_ok=True)


# ── Tool wrapper tests ───────────────────────────────────────────────────


class DesktopRunnerToolTests(DesktopWorkspaceTestCase):
    async def test_file_write_writes_real_file_in_workspace(self) -> None:
        result = await call_tool(
            self.workspace_root,
            "file_write",
            {"path": "hello.txt", "content": "hello desktop runner"},
        )
        self.assertIn("hello.txt", result)
        written = (self.workspace_root / "hello.txt").read_text(encoding="utf-8")
        self.assertEqual(written, "hello desktop runner")

    async def test_tool_result_truncation_is_applied(self) -> None:
        big = "x" * (DESKTOP_TOOL_RESULT_MAX_CHARS + 1000)
        (self.workspace_root / "big.txt").write_text(big, encoding="utf-8")
        result = await call_tool(self.workspace_root, "file_read", {"path": "big.txt"})
        self.assertIn("...[截断]", result)
        self.assertLess(len(result), len(big))
        self.assertTrue(result.startswith("xxxxx"))

    async def test_paths_outside_workspace_root_are_rejected(self) -> None:
        outside_relative = await call_tool(
            self.workspace_root,
            "file_write",
            {"path": "../escape.txt", "content": "nope"},
        )
        self.assertIn("超出桌面工作区允许范围", outside_relative)
        self.assertFalse((self.workspace_root.parent / "escape.txt").exists())

        outside_absolute = self.workspace_root.parent / "elsewhere.txt"
        outside_absolute.write_text("secret", encoding="utf-8")
        result = await call_tool(
            self.workspace_root,
            "file_read",
            {"path": str(outside_absolute)},
        )
        self.assertIn("超出桌面工作区允许范围", result)

    async def test_absolute_path_inside_workspace_root_is_allowed(self) -> None:
        target = self.workspace_root / "nested" / "ok.txt"
        result = await call_tool(
            self.workspace_root,
            "file_write",
            {"path": str(target), "content": "inside"},
        )
        self.assertNotIn("超出桌面工作区允许范围", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "inside")

    async def test_desktop_whitelist_has_exactly_seven_file_tools(self) -> None:
        tools = build_desktop_runner_tools(self.workspace_root)
        self.assertEqual(
            [tool.name for tool in tools],
            [
                "file_read",
                "file_write",
                "file_edit",
                "file_write_batch",
                "mkdir",
                "file_glob",
                "file_search",
            ],
        )


# ── Settings gating ──────────────────────────────────────────────────────


class DesktopRunnerSettingsTests(unittest.TestCase):
    def test_env_unset_disables_the_runner(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(env={})
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.base_url, "http://127.0.0.1:28000")
        self.assertEqual(settings.workspace_id, WORKSPACE_ID)
        self.assertIsNone(settings.workspace_root)

    def test_env_enables_the_runner_with_overrides(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(
            env={
                ENABLE_ENV: "1",
                "AGENTHUB_DESKTOP_LOCAL_RUNNER_BASE_URL": "http://127.0.0.1:39000/",
                "AGENTHUB_DESKTOP_WORKSPACE_ROOT": str(Path(tempfile.gettempdir()) / "proj"),
                "AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL": "glm-4",
                "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_ITERATIONS": "4",
                "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOTAL_TOKENS": "1000",
            }
        )
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.base_url, "http://127.0.0.1:39000")
        self.assertEqual(
            settings.workspace_root, Path(tempfile.gettempdir()) / "proj"
        )
        self.assertEqual(settings.model_name, "glm-4")
        self.assertEqual(settings.max_iterations, 4)
        self.assertEqual(settings.max_total_tokens, 1000)

    def test_token_without_user_identity_is_rejected(self) -> None:
        with self.assertRaisesRegex(Exception, "USER_ID"):
            DesktopLocalRunnerSettings.from_env(
                env={"AGENTHUB_DESKTOP_RUNNER_TOKEN": "abc"}
            )

    def test_default_workspace_root_is_under_workspaces_dir(self) -> None:
        settings = desktop_settings()
        root = settings.default_workspace_root()
        self.assertEqual(root.relative_to(WORKSPACES_DIR), root.relative_to(WORKSPACES_DIR))
        self.assertTrue(str(root).startswith(str(WORKSPACES_DIR)))


class DesktopRunnerLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_env_unset_does_not_start_any_controller(self) -> None:
        app = SimpleNamespace(state=SimpleNamespace())
        factory_calls: list[Any] = []

        await startup_desktop_local_runner(
            app,
            settings=DesktopLocalRunnerSettings.from_env(env={}),
            controller_factory=factory_calls.append,
        )
        self.assertEqual(factory_calls, [])
        self.assertIsNone(getattr(app.state, "desktop_local_runner", None))

        await shutdown_desktop_local_runner(app)

    async def test_enabled_env_starts_and_stops_injected_controller(self) -> None:
        app = SimpleNamespace(state=SimpleNamespace())
        events: list[str] = []

        class StubController:
            async def start(self) -> None:
                events.append("start")

            async def stop(self) -> None:
                events.append("stop")

        def factory(settings: DesktopLocalRunnerSettings) -> StubController:
            del settings
            events.append("build")
            return StubController()

        await startup_desktop_local_runner(
            app,
            settings=desktop_settings(enabled=True),
            controller_factory=factory,
        )
        self.assertEqual(events, ["build", "start"])

        await shutdown_desktop_local_runner(app)
        self.assertEqual(events, ["build", "start", "stop"])
        self.assertIsNone(app.state.desktop_local_runner)


# ── Derivation ───────────────────────────────────────────────────────────


class FakeMissionSource:
    def __init__(
        self,
        missions: list[Any],
        existing_kinds: set[str] | None = None,
    ) -> None:
        self.missions = missions
        self.existing_kinds = existing_kinds or set()
        self.created: list[str] = []

    async def running_manual_missions(self, workspace_id: str) -> list[Any]:
        del workspace_id
        return list(self.missions)

    async def has_work_unit_kind(self, mission_id: str, kind: str) -> bool:
        return f"{mission_id}:{kind}" in self.existing_kinds

    async def create_desktop_task_work_unit(self, mission_id: str) -> str:
        self.created.append(mission_id)
        return f"wu-{mission_id}"


class DesktopWorkUnitDerivationTests(unittest.IsolatedAsyncioTestCase):
    async def test_derives_exactly_one_work_unit_per_eligible_mission(self) -> None:
        source = FakeMissionSource(missions=[SimpleNamespace(id="mis-1")])
        derived = await derive_desktop_task_work_units(
            source, workspace_id=WORKSPACE_ID
        )
        self.assertEqual(derived, ["wu-mis-1"])
        self.assertEqual(source.created, ["mis-1"])

    async def test_derivation_is_idempotent_even_after_a_previous_unit(self) -> None:
        source = FakeMissionSource(missions=[SimpleNamespace(id="mis-1")])
        await derive_desktop_task_work_units(source, workspace_id=WORKSPACE_ID)
        source.existing_kinds.add("mis-1:desktop.task")
        derived = await derive_desktop_task_work_units(
            source, workspace_id=WORKSPACE_ID
        )
        self.assertEqual(derived, [])
        self.assertEqual(source.created, ["mis-1"])


# ── End-to-end composition with fakes ────────────────────────────────────


class ScriptedModel:
    """First turn: file_write tool call; second turn: final summary text."""

    def __init__(self) -> None:
        self.turns = 0
        self.observed_tool_results: list[tuple[Any, ...]] = []

    async def complete(self, request, tool_results):
        del request
        self.turns += 1
        self.observed_tool_results.append(tool_results)
        if not tool_results:
            return ModelResponse(
                tool_calls=(
                    FunctionCall(
                        id="call-1",
                        name="file_write",
                        arguments={
                            "path": "hello.txt",
                            "content": "hello desktop runner",
                        },
                    ),
                ),
                usage=ModelUsage(prompt_tokens=10, completion_tokens=10),
            )
        return ModelResponse(
            content="已创建 hello.txt",
            usage=ModelUsage(prompt_tokens=10, completion_tokens=10),
        )


class StaticModelFactory:
    def __init__(self, model: ScriptedModel) -> None:
        self.model = model
        self.tool_sets: list[list[str]] = []

    def build(self, tools):
        self.tool_sets.append([tool.name for tool in tools])
        return self.model


class FakeDesktopControl:
    """In-memory Mission Control for one desktop.task WorkUnit."""

    def __init__(self, claimed: dict[str, Any], context: dict[str, Any]) -> None:
        self.claimed = claimed
        self.context = context
        self.claimed_once = False
        self.calls: list[str] = []
        self.statuses: list[str] = ["PENDING"]
        self.artifacts: list[PublishedArtifact] = []
        self.artifact_refs: list[dict[str, str]] = []
        self.failure_reason: str | None = None
        self.last_workspace_id: str | None = None
        self.done = asyncio.Event()

    async def claim_ready_work_unit(
        self,
        workspace_id: str,
        *,
        runner_id: str,
        agent_id: str,
        adapter_type: str,
        supported_work_unit_kinds: tuple[str, ...],
        lease_seconds: int,
    ) -> dict[str, Any]:
        del runner_id, lease_seconds
        self.calls.append("claim")
        self.last_workspace_id = workspace_id
        if self.claimed_once:
            return {"claimStatus": "idle", "workUnit": None}
        self.claimed_once = True
        assert DESKTOP_AGENT_ID == agent_id
        assert DESKTOP_ADAPTER_TYPE == adapter_type
        assert "desktop.task" in supported_work_unit_kinds
        self.statuses.append("LEASED")
        return copy.deepcopy(self.claimed)

    async def get_execution_context(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
    ) -> dict[str, Any]:
        del runner_id, lease_id
        self.calls.append("context")
        return {"executionContext": copy.deepcopy(self.context)}

    async def start_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
    ) -> dict[str, Any]:
        del mission_id, work_unit_id, runner_id, lease_id
        self.calls.append("start")
        self.statuses.append("RUNNING")
        return self._with_status("RUNNING")

    async def heartbeat_work_unit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return self._with_status("RUNNING")

    async def record_execution_checkpoint(
        self,
        mission_id: str,
        work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(f"checkpoint:{kwargs['sequence']}")
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
            "createdBy": {"id": RUNNER_USER_ID, "type": "service"},
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }

    async def register_artifact(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        artifact: PublishedArtifact,
        artifact_id: str,
        kind: str,
        media_type: str,
    ) -> dict[str, Any]:
        del mission_id, work_unit_id, runner_id, lease_id, kind, media_type
        self.calls.append("register")
        self.artifacts.append(artifact)
        return {"id": artifact_id}

    async def complete_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        artifact_refs: list[dict[str, str]],
    ) -> dict[str, Any]:
        del mission_id, work_unit_id, runner_id, lease_id
        self.calls.append("complete")
        self.artifact_refs = artifact_refs
        self.statuses.append("SUCCEEDED")
        self.done.set()
        return self._with_status("SUCCEEDED")

    async def fail_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        reason: str,
    ) -> dict[str, Any]:
        del mission_id, work_unit_id, runner_id, lease_id
        self.calls.append("fail")
        self.failure_reason = reason
        self.statuses.append("FAILED")
        self.done.set()
        return self._with_status("FAILED")

    def _with_status(self, status: str) -> dict[str, Any]:
        payload = copy.deepcopy(self.claimed["workUnit"])
        payload["status"] = status
        return payload


class FakePublisher:
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

    async def publish_file(self, path: Path) -> PublishedArtifact:
        return await self.publish_bytes(path.read_bytes())


class IdleMissionSource:
    """Derivation source for composition tests: no missions, no side effects."""

    async def running_manual_missions(self, workspace_id: str) -> list[Any]:
        del workspace_id
        return []

    async def has_work_unit_kind(self, mission_id: str, kind: str) -> bool:
        del mission_id, kind
        return False

    async def create_desktop_task_work_unit(self, mission_id: str) -> str:
        raise AssertionError("derivation must not create units without missions")


def desktop_claim_payload() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the claimed WorkUnit payload and execution context from domain models."""
    lease = Lease(
        id="lease-1",
        runner_id=RUNNER_USER_ID,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
    )
    work_unit = WorkUnit(
        id="wu-desktop-1",
        mission_id="mis-desktop-1",
        assigned_agent_id=DESKTOP_AGENT_ID,
        kind="desktop.task",
        dependencies=(),
        input_refs=(),
        expected_outputs=(OutputSpec(kind="text", required=False),),
        required_capabilities=(),
        assigned_adapter=DESKTOP_ADAPTER_TYPE,
        status=WorkUnitStatus.LEASED,
        attempt=1,
        lease=lease,
    )
    contract = MissionContract(
        id="contract-manual-v1",
        version=1,
        repository_scopes=(),
        allowed_capabilities=(),
        budgets=Budgets(time_seconds=300, model_cost=1, retries=0),
        acceptance_criteria=(
            AcceptanceCriterion(
                id="manual-review",
                kind=CriterionKind.MANUAL,
                description="由工作空间操作者审核输出",
                required=True,
            ),
        ),
        decision_gates=(),
        forbidden_actions=(),
    )
    now = datetime.now(timezone.utc)
    mission = Mission(
        id="mis-desktop-1",
        workspace_id=WORKSPACE_ID,
        title="桌面任务",
        objective="在工作区创建 hello.txt，内容为 hello desktop runner",
        source=MissionSource(type=MissionSourceType.MANUAL),
        contract_id="contract-manual-v1",
        contract_version=1,
        status=MissionStatus.RUNNING,
        created_by=ActorRef(type=ActorType.HUMAN, id=WORKSPACE_ID),
        created_at=now,
        updated_at=now,
    )
    claimed = {
        "claimStatus": "claimed",
        "workUnit": work_unit.to_public_dict(),
    }
    context = {
        "version": 1,
        "mission": mission.to_public_dict(),
        "contract": contract.to_public_dict(),
        "workUnit": work_unit.to_public_dict(),
    }
    return claimed, context


class DesktopLocalRunnerCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_desktop_task_runs_created_to_succeeded_and_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp) / "workspace"
            workspace_root.mkdir(parents=True)
            claimed, context = desktop_claim_payload()
            control = FakeDesktopControl(claimed, context)
            publisher = FakePublisher()
            model_factory = StaticModelFactory(ScriptedModel())
            controller = DesktopLocalRunnerController(
                desktop_settings(workspace_root=workspace_root),
                control=control,
                publisher=publisher,
                model_factory=model_factory,
                mission_source=IdleMissionSource(),
                workspace_root=workspace_root,
            )

            await controller.start()
            try:
                await asyncio.wait_for(control.done.wait(), timeout=5)
            finally:
                await controller.stop()

            self.assertEqual(
                control.statuses, ["PENDING", "LEASED", "RUNNING", "SUCCEEDED"]
            )
            self.assertEqual(
                control.calls[:3],
                ["claim", "context", "start"],
            )
            self.assertIn("register", control.calls)
            self.assertIn("complete", control.calls)
            self.assertNotIn("fail", control.calls)
            self.assertEqual(control.last_workspace_id, WORKSPACE_ID)
            self.assertEqual(
                control.artifact_refs[0]["digest"],
                control.artifacts[0].digest,
            )
            self.assertTrue((workspace_root / "hello.txt").exists())
            self.assertEqual(
                (workspace_root / "hello.txt").read_text(encoding="utf-8"),
                "hello desktop runner",
            )
            # The published artifact carries the final model output.
            self.assertIn("已创建 hello.txt", publisher.contents[-1].decode("utf-8"))
            # The scripted model observed the rendered tool result as text.
            self.assertIn(
                "成功",
                model_factory.model.observed_tool_results[1][0].content,
            )
            # The desktop whitelist is bound as the request-scoped tool set.
            self.assertEqual(
                model_factory.tool_sets[0],
                [
                    "file_read",
                    "file_write",
                    "file_edit",
                    "file_write_batch",
                    "mkdir",
                    "file_glob",
                    "file_search",
                ],
            )


if __name__ == "__main__":
    unittest.main()
