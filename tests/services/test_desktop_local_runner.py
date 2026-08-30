from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from app.config import WORKSPACES_DIR
from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain import (
    AcceptanceCriterion,
    ActorRef,
    ActorType,
    Budgets,
    CriterionKind,
    EvidenceVerdict,
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
from app.repositories import MissionRepository
from app.services.artifact_integrity_service import (
    ContentAddressedArtifactByteVerifier,
)
from app.services.artifact_store_service import PublishedArtifact
from app.services.desktop_local_runner import (
    CONTEXT_CHAR_BUDGET_ENV,
    DESKTOP_ADAPTER_TYPE,
    DESKTOP_AGENT_ID,
    DESKTOP_RUNNER_LABEL,
    DESKTOP_VERIFIER_ID,
    ENABLE_ENV,
    MCP_CONFIG_ENV,
    RUN_COMMAND_MARKER,
    VERIFY_COMMAND_TIMEOUT_ENV,
    VERIFY_ENV,
    VERIFY_INTERVAL_ENV,
    WORKERS_ENV,
    DesktopLocalRunnerController,
    DesktopLocalRunnerSettings,
    DesktopModelConfig,
    DesktopModelFactory,
    derive_desktop_task_work_units,
    extract_run_commands,
    extract_verify_commands,
    run_verify_command,
    shutdown_desktop_local_runner,
    startup_desktop_local_runner,
)
from app.services.desktop_runner_tools import (
    DELEGATE_SUBTASK_DEFAULT_ITERATIONS,
    DELEGATE_SUBTASK_MAX_ITERATIONS,
    DELEGATE_SUBTASK_RESULT_MAX_CHARS,
    DESKTOP_CODE_EXECUTE_MAX_TIMEOUT,
    DESKTOP_TOOL_RESULT_MAX_CHARS,
    _clamp_desktop_timeout,
    build_desktop_runner_tools,
)
from app.services.harness_service import (
    FunctionCall,
    ModelResponse,
    ModelUsage,
)
from app.services.model_port import ModelAdapterPort
from app.services.mission_service import MissionService
from app.services.workspace_admission_service import (
    DatabaseWorkspaceClaimAdmissionPolicyResolver,
    WorkspaceClaimAdmissionPolicy,
    WorkspaceClaimStatus,
)
from tests.domain.factories import build_mission, build_work_unit

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
        "verify_enabled": False,
        "verify_interval_seconds": 0.01,
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

    async def test_desktop_whitelist_binds_builtin_and_memory_tools(self) -> None:
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
                "code_execute",
                "memory_save",
                "memory_search",
                "command_execute",
                "lint_check",
            ],
        )

    async def test_delegate_subtask_appended_only_with_model_factory(self) -> None:
        factory = _SubtaskModelFactory(ModelResponse(content="done"))
        tools = build_desktop_runner_tools(self.workspace_root, model_factory=factory)
        self.assertEqual(tools[-1].name, "delegate_subtask")
        self.assertNotIn("delegate_subtask", [tool.name for tool in tools[:-1]])

        without_factory = build_desktop_runner_tools(self.workspace_root)
        self.assertNotIn(
            "delegate_subtask", [tool.name for tool in without_factory]
        )

    # ── code_execute desktop profile (G5) ─────────────────────────────

    async def test_memory_save_and_search_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as memory_tmp:
            memory_dir = Path(memory_tmp)
            with (
                patch("app.config.MEMORY_DIR", memory_dir),
                patch(
                    "app.services.tools.builtin_tools.MEMORY_DIR", memory_dir
                ),
            ):
                saved = await call_tool(
                    self.workspace_root,
                    "memory_save",
                    {
                        "name": "desktop-runner-note",
                        "content": "桌面 runner 的项目偏好记忆",
                        "type": "project",
                        "description": "G8 memory whitelist",
                    },
                )
                self.assertIn("desktop-runner-note", saved)
                self.assertIn("桌面 runner 的项目偏好记忆", saved)
                searched = await call_tool(
                    self.workspace_root,
                    "memory_search",
                    {"query": "desktop-runner-note"},
                )
                self.assertIn("desktop-runner-note", searched)
                self.assertIn("G8 memory whitelist", searched)

    async def test_memory_save_rejects_empty_content(self) -> None:
        with tempfile.TemporaryDirectory() as memory_tmp:
            memory_dir = Path(memory_tmp)
            with (
                patch("app.config.MEMORY_DIR", memory_dir),
                patch(
                    "app.services.tools.builtin_tools.MEMORY_DIR", memory_dir
                ),
            ):
                result = await call_tool(
                    self.workspace_root,
                    "memory_save",
                    {"name": "empty", "content": "  "},
                )
        self.assertIn("记忆内容不能为空", result)

    async def test_code_execute_runs_python_and_returns_output(self) -> None:
        result = await call_tool(
            self.workspace_root,
            "code_execute",
            {"code": "print('hello desktop exec')", "language": "python"},
        )
        self.assertIn("hello desktop exec", result)
        self.assertNotIn("工具执行失败", result)

    async def test_code_execute_relative_paths_land_inside_workspace(self) -> None:
        await call_tool(
            self.workspace_root,
            "file_write",
            {"path": "notes.txt", "content": "from the workspace"},
        )
        result = await call_tool(
            self.workspace_root,
            "code_execute",
            {"code": "print(open('notes.txt', encoding='utf-8').read())"},
        )
        self.assertIn("from the workspace", result)

    async def test_code_execute_cwd_is_confined_to_workspace_root(self) -> None:
        result = await call_tool(
            self.workspace_root,
            "code_execute",
            {
                "code": "import os; print(os.path.basename(os.getcwd()))",
                "cwd": "sub",
            },
        )
        self.assertIn("sub", result)
        self.assertTrue((self.workspace_root / "sub").is_dir())

        escape = await call_tool(
            self.workspace_root,
            "code_execute",
            {"code": "print('nope')", "cwd": ".."},
        )
        self.assertIn("超出桌面工作区允许范围", escape)

    async def test_code_execute_timeout_is_enforced(self) -> None:
        result = await call_tool(
            self.workspace_root,
            "code_execute",
            {"code": "import time; time.sleep(5)", "language": "python", "timeout": 1},
        )
        self.assertIn("超时", result)

    def test_code_execute_timeout_is_clamped_to_desktop_ceiling(self) -> None:
        self.assertEqual(_clamp_desktop_timeout(90), DESKTOP_CODE_EXECUTE_MAX_TIMEOUT)
        self.assertEqual(_clamp_desktop_timeout(60), DESKTOP_CODE_EXECUTE_MAX_TIMEOUT)
        self.assertEqual(_clamp_desktop_timeout(30), 30)

    async def test_code_execute_permission_denial_fails_closed(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from app.services.tools.permission import PermissionBehavior

        class DenyingManager:
            def add_rule(self, rule: object) -> None:
                del rule

            async def check(self, *args: object, **kwargs: object):
                return SimpleNamespace(
                    behavior=PermissionBehavior.DENY,
                    reason="denied by stub",
                    source="test:stub",
                )

        with patch(
            "app.services.tools.permission.PermissionManager", DenyingManager
        ):
            result = await call_tool(
                self.workspace_root,
                "code_execute",
                {"code": "print('must not run')"},
            )
        self.assertIn("桌面本地策略未批准 code_execute", result)
        self.assertNotIn("must not run", result)

    # ── command_execute (P1-3): denial-only shell tool ────────────────

    async def test_command_execute_is_denial_only(self) -> None:
        result = await call_tool(
            self.workspace_root,
            "command_execute",
            {"command": "echo pwned"},
        )
        self.assertIn("工具执行失败", result)
        self.assertIn("RUN:", result)
        self.assertIn("验收", result)
        self.assertNotIn("pwned", result.replace("echo pwned", ""))

    def test_command_execute_arguments_are_validated(self) -> None:
        tools = {
            tool.name: tool
            for tool in build_desktop_runner_tools(self.workspace_root)
        }
        validate = tools["command_execute"].validate_arguments
        self.assertEqual(validate({"command": " ls -la "}), {"command": "ls -la"})
        with self.assertRaises(ValueError):
            validate({"command": "   "})
        with self.assertRaises(ValueError):
            validate({})

    # ── lint_check (P2-3): zero-dependency syntax diagnostics ─────────

    async def test_lint_check_returns_ok_for_valid_python(self) -> None:
        (self.workspace_root / "good.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8"
        )
        result = await call_tool(
            self.workspace_root, "lint_check", {"path": "good.py"}
        )
        self.assertTrue(result.startswith("OK"))

    async def test_lint_check_reports_syntax_error_location(self) -> None:
        (self.workspace_root / "bad.py").write_text(
            "def add(a, b):\n    return a + :\n", encoding="utf-8"
        )
        result = await call_tool(
            self.workspace_root, "lint_check", {"path": "bad.py"}
        )
        self.assertIn("语法错误", result)
        self.assertIn("行 2", result)
        self.assertIn("bad.py", result)

    async def test_lint_check_rejects_missing_and_non_python_paths(self) -> None:
        missing = await call_tool(
            self.workspace_root, "lint_check", {"path": "nope.py"}
        )
        self.assertIn("文件不存在", missing)

        (self.workspace_root / "note.txt").write_text("plain", encoding="utf-8")
        wrong_type = await call_tool(
            self.workspace_root, "lint_check", {"path": "note.txt"}
        )
        self.assertIn("仅支持 .py", wrong_type)

    async def test_lint_check_rejects_paths_outside_workspace(self) -> None:
        outside = self.workspace_root.parent / "outside.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        result = await call_tool(
            self.workspace_root, "lint_check", {"path": str(outside)}
        )
        self.assertIn("超出桌面工作区允许范围", result)


# ── delegate_subtask (G8) ────────────────────────────────────────────────


class _SubtaskModel:
    """Scripted ModelPort for one delegate_subtask child harness."""

    def __init__(self, response: ModelResponse) -> None:
        self._response = response
        self.requests: list[str] = []

    async def complete(
        self,
        request: Any,
        tool_results: tuple[Any, ...],
        *,
        tools_enabled: bool = True,
    ) -> ModelResponse:
        self.requests.append(request.code)
        return self._response


class _SubtaskModelFactory:
    """Capturing HarnessModelFactoryPort double for delegate tests."""

    def __init__(
        self,
        response: ModelResponse | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._response = response or ModelResponse(content="子任务总结")
        self._error = error
        self.tool_sets: list[list[str]] = []

    def build(self, tools: Any) -> Any:
        self.tool_sets.append([tool.name for tool in tools])
        return _SubtaskModel(self._response)


class DesktopDelegateSubtaskTests(DesktopWorkspaceTestCase):
    async def test_delegate_subtask_returns_child_summary(self) -> None:
        factory = _SubtaskModelFactory(
            ModelResponse(content="子任务完成：calc.py 实现正确。")
        )
        tools = {
            tool.name: tool
            for tool in build_desktop_runner_tools(
                self.workspace_root, model_factory=factory
            )
        }
        result = await tools["delegate_subtask"].handler(
            {"objective": "检查 calc.py 的内容并给出评价"}
        )
        self.assertIn("子任务完成：calc.py 实现正确。", result)
        # The child harness ran on the objective through the parent factory
        # with every tool except delegate_subtask itself.
        self.assertEqual(factory.tool_sets, [list(tools)[:-1]])

    async def test_delegate_subtask_toolset_excludes_itself(self) -> None:
        factory = _SubtaskModelFactory()
        tools = build_desktop_runner_tools(self.workspace_root, model_factory=factory)
        await tools[-1].handler({"objective": "任意子任务"})
        # Recursion-proofing: the child toolset never contains
        # delegate_subtask, so a sub agent cannot spawn further agents.
        self.assertEqual(len(factory.tool_sets), 1)
        self.assertNotIn("delegate_subtask", factory.tool_sets[0])
        self.assertEqual(len(factory.tool_sets[0]), len(tools) - 1)

    async def test_delegate_subtask_failure_is_surfaced_to_parent(self) -> None:
        class ExplodingModel:
            async def complete(self, *args: Any, **kwargs: Any) -> ModelResponse:
                raise RuntimeError("model channel down")

        class ExplodingFactory:
            def build(self, tools: Any) -> Any:
                return ExplodingModel()

        tools = {
            tool.name: tool
            for tool in build_desktop_runner_tools(
                self.workspace_root, model_factory=ExplodingFactory()
            )
        }
        result = await tools["delegate_subtask"].handler({"objective": "子任务"})
        self.assertIn("子任务执行失败", result)
        self.assertIn("RuntimeError", result)

    def test_delegate_subtask_arguments_are_validated_and_clamped(self) -> None:
        tools = {
            tool.name: tool
            for tool in build_desktop_runner_tools(
                self.workspace_root,
                model_factory=_SubtaskModelFactory(),
            )
        }
        validate = tools["delegate_subtask"].validate_arguments
        self.assertEqual(
            validate({"objective": "目标"})["max_iterations"],
            DELEGATE_SUBTASK_DEFAULT_ITERATIONS,
        )
        self.assertEqual(
            validate({"objective": "目标", "max_iterations": 99})["max_iterations"],
            DELEGATE_SUBTASK_MAX_ITERATIONS,
        )
        self.assertEqual(
            validate({"objective": "目标", "max_iterations": 0})["max_iterations"],
            1,
        )
        with self.assertRaises(ValueError):
            validate({"objective": "   "})
        with self.assertRaises(ValueError):
            validate({"max_iterations": 2})
        with self.assertRaises(ValueError):
            validate({"objective": "目标", "max_iterations": "many"})

    async def test_delegate_subtask_truncates_oversized_summary(self) -> None:
        factory = _SubtaskModelFactory(
            ModelResponse(content="x" * (DELEGATE_SUBTASK_RESULT_MAX_CHARS + 500))
        )
        tools = {
            tool.name: tool
            for tool in build_desktop_runner_tools(
                self.workspace_root, model_factory=factory
            )
        }
        result = await tools["delegate_subtask"].handler({"objective": "子任务"})
        self.assertLessEqual(len(result), DELEGATE_SUBTASK_RESULT_MAX_CHARS + 40)
        self.assertIn("...[截断]", result)


# ── Settings gating ──────────────────────────────────────────────────────


class DesktopRunnerSettingsTests(unittest.TestCase):
    def test_env_unset_disables_the_runner(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(env={})
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.base_url, "http://127.0.0.1:28000")
        self.assertEqual(settings.workspace_id, WORKSPACE_ID)
        self.assertIsNone(settings.workspace_root)

    def test_verification_defaults_to_on_with_five_second_interval(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(env={})
        self.assertTrue(settings.verify_enabled)
        self.assertEqual(settings.verify_interval_seconds, 5.0)

    def test_verification_can_be_disabled_and_tuned_via_env(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(
            env={
                VERIFY_ENV: "0",
                VERIFY_INTERVAL_ENV: "2.5",
            }
        )
        self.assertFalse(settings.verify_enabled)
        self.assertEqual(settings.verify_interval_seconds, 2.5)

    def test_non_positive_verify_interval_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            DesktopLocalRunnerSettings.from_env(env={VERIFY_INTERVAL_ENV: "0"})

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

    def test_context_char_budget_defaults_to_24000(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(env={})
        self.assertEqual(settings.context_char_budget, 24_000)

    def test_context_char_budget_is_env_configurable(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(
            env={CONTEXT_CHAR_BUDGET_ENV: "4096"}
        )
        self.assertEqual(settings.context_char_budget, 4096)

    def test_non_positive_context_char_budget_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            DesktopLocalRunnerSettings.from_env(env={CONTEXT_CHAR_BUDGET_ENV: "0"})

    def test_verify_command_timeout_defaults_to_120_seconds(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(env={})
        self.assertEqual(settings.verify_command_timeout_seconds, 120.0)

    def test_verify_command_timeout_is_env_configurable(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(
            env={VERIFY_COMMAND_TIMEOUT_ENV: "30"}
        )
        self.assertEqual(settings.verify_command_timeout_seconds, 30.0)

    def test_non_positive_verify_command_timeout_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            DesktopLocalRunnerSettings.from_env(env={VERIFY_COMMAND_TIMEOUT_ENV: "0"})

    def test_default_workspace_root_is_under_workspaces_dir(self) -> None:
        settings = desktop_settings()
        root = settings.default_workspace_root()
        self.assertEqual(root.relative_to(WORKSPACES_DIR), root.relative_to(WORKSPACES_DIR))
        self.assertTrue(str(root).startswith(str(WORKSPACES_DIR)))

    # ── P1-2 system prompt guidance ─────────────────────────────────────

    def test_desktop_system_prompt_mentions_memory_search(self) -> None:
        from app.services.desktop_local_runner import DESKTOP_SYSTEM_PROMPT

        self.assertIn("memory_search", DESKTOP_SYSTEM_PROMPT)
        self.assertIn("历史任务记忆", DESKTOP_SYSTEM_PROMPT)

    # ── P2-2 worker count settings ──────────────────────────────────────

    def test_workers_default_to_one(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(env={})
        self.assertEqual(settings.workers, 1)

    def test_workers_are_env_configurable_within_ceiling(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(env={WORKERS_ENV: "3"})
        self.assertEqual(settings.workers, 3)

    def test_worker_count_above_ceiling_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            DesktopLocalRunnerSettings.from_env(env={WORKERS_ENV: "5"})

    def test_non_positive_worker_count_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            DesktopLocalRunnerSettings.from_env(env={WORKERS_ENV: "0"})

    # ── P2-1 MCP config settings ────────────────────────────────────────

    def test_mcp_config_defaults_to_none(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(env={})
        self.assertIsNone(settings.mcp_config)

    def test_mcp_config_is_env_configurable(self) -> None:
        settings = DesktopLocalRunnerSettings.from_env(
            env={MCP_CONFIG_ENV: "C:/cfg/mcp.json"}
        )
        self.assertEqual(settings.mcp_config, Path("C:/cfg/mcp.json"))


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
            # The desktop whitelist is bound as the request-scoped tool set,
            # including the G8 memory tools, the P1-3 command_execute denial
            # tool, the P2-3 lint_check tool, and the model-backed
            # delegate_subtask spawn tool.
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
                    "code_execute",
                    "memory_save",
                    "memory_search",
                    "command_execute",
                    "lint_check",
                    "delegate_subtask",
                ],
            )


# ── Context compression wiring ───────────────────────────────────────────


class DesktopModelContextBudgetTests(DesktopWorkspaceTestCase):
    def test_model_factory_builds_port_with_configured_budget(self) -> None:
        # The adapter manager import chain pulls optional plugin deps; the
        # budget contract under test only needs a resolvable adapter stub.
        fake_manager_module = ModuleType("app.services.adapter_manager")
        fake_manager_module.adapter_manager = SimpleNamespace(
            get_adapter=lambda provider: object()
        )
        with patch.dict(
            sys.modules, {"app.services.adapter_manager": fake_manager_module}
        ):
            factory = DesktopModelFactory(
                DesktopModelConfig(
                    provider="mock", model="test-model", api_key="", base_url=""
                ),
                context_char_budget=1234,
            )
            port = factory.build([])

        self.assertIsInstance(port, ModelAdapterPort)
        self.assertEqual(port._context_char_budget, 1234)

    async def test_controller_passes_settings_budget_to_model_factory(self) -> None:
        with (
            patch(
                "app.services.desktop_local_runner.load_default_model_config",
                new=AsyncMock(
                    return_value=DesktopModelConfig(
                        provider="mock", model="test-model", api_key="", base_url=""
                    )
                ),
            ),
            patch(
                "app.services.desktop_local_runner.DesktopModelFactory"
            ) as factory_cls,
        ):
            controller = DesktopLocalRunnerController(
                desktop_settings(context_char_budget=4321),
                control=_IdleControl(),
                publisher=FakePublisher(),
                mission_source=IdleMissionSource(),
                workspace_root=self.workspace_root,
            )

            await controller.start()
            try:
                factory_cls.assert_called_once()
                self.assertEqual(
                    factory_cls.call_args.kwargs.get("context_char_budget"), 4321
                )
            finally:
                await controller.stop()


# ── Unattended verification loop ─────────────────────────────────────────


class _IdleControl:
    """Runner control for verification tests: never claims work."""

    async def claim_ready_work_unit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"claimStatus": "idle", "workUnit": None}


class FakeVerifierControl:
    def __init__(self, discovery: dict[str, Any] | None) -> None:
        self.discovery = discovery
        self.discover_calls: list[str] = []
        self.submissions: list[Any] = []
        self.submitted = asyncio.Event()

    async def discover_verification_work(
        self, workspace_id: str
    ) -> dict[str, Any]:
        self.discover_calls.append(workspace_id)
        if self.discovery is None:
            return {"discoveryStatus": "idle", "verificationContext": None}
        return self.discovery

    async def submit_verification(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        submission: Any,
    ) -> dict[str, Any]:
        self.submissions.append((mission_id, work_unit_id, submission))
        self.submitted.set()
        return {}


def artifact_set_policy() -> dict[str, Any]:
    return {
        "status": "ready",
        "criterionId": "desktop-artifacts",
        "evaluator": "artifact-set.v1",
        "configurationDigest": "sha256:" + "b" * 64,
        "parameters": {"minimumArtifacts": 1, "requiredArtifactKinds": ["report"]},
    }


def ready_discovery(
    digest: str,
    size_bytes: int,
    content_address: str,
    policy: dict[str, Any] | None = None,
    objective: str = "创建 notes.txt",
) -> dict[str, Any]:
    return {
        "discoveryStatus": "ready",
        "verificationContext": {
            "version": 3,
            "mission": {
                "id": "mis-desktop-1",
                "title": "桌面任务",
                "objective": objective,
            },
            "contract": {
                "id": "contract-manual-v1",
                "version": 1,
                "acceptanceCriteria": [],
            },
            "workUnit": {
                "id": "wu-desktop-1",
                "kind": "desktop.task",
                "inputRefs": [],
                "expectedOutputs": [],
                "status": "VERIFYING",
                "attempt": 1,
            },
            "artifacts": [
                {
                    "id": "art-1",
                    "attempt": 1,
                    "kind": "report",
                    "digest": digest,
                    "contentAddress": content_address,
                    "mediaType": "text/plain",
                    "sizeBytes": size_bytes,
                    "sensitivity": "internal",
                }
            ],
            "evaluationPolicy": policy or artifact_set_policy(),
        },
    }


class DesktopVerificationLoopTests(DesktopWorkspaceTestCase):
    def _artifact_root(self) -> Path:
        root = Path(self._tmp.name) / "artifacts"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _byte_verifier(self) -> ContentAddressedArtifactByteVerifier:
        settings = SimpleNamespace(
            local_root=self._artifact_root(),
            verify_max_bytes=1024 * 1024,
        )
        return ContentAddressedArtifactByteVerifier(settings)

    def _store_artifact(self, content: bytes) -> tuple[str, int, str]:
        digest = hashlib.sha256(content).hexdigest()
        sha_dir = self._artifact_root() / "sha256"
        sha_dir.mkdir(parents=True, exist_ok=True)
        (sha_dir / digest).write_bytes(content)
        return f"sha256:{digest}", len(content), f"local:sha256/{digest}"

    async def _run_controller(
        self,
        verifier: FakeVerifierControl,
        **setting_overrides: Any,
    ) -> DesktopLocalRunnerController:
        controller = DesktopLocalRunnerController(
            desktop_settings(verify_enabled=True, **setting_overrides),
            control=_IdleControl(),
            publisher=FakePublisher(),
            model_factory=StaticModelFactory(ScriptedModel()),
            mission_source=IdleMissionSource(),
            workspace_root=self.workspace_root,
            verifier_control=verifier,
            byte_verifier=self._byte_verifier(),
        )
        await controller.start()
        return controller

    async def test_ready_artifact_set_item_submits_pass_evidence(self) -> None:
        digest, size, address = self._store_artifact(b"notes content")
        verifier = FakeVerifierControl(ready_discovery(digest, size, address))
        controller = await self._run_controller(verifier)
        try:
            await asyncio.wait_for(verifier.submitted.wait(), timeout=2)
        finally:
            await controller.stop()

        self.assertEqual(verifier.discover_calls, [WORKSPACE_ID])
        self.assertEqual(len(verifier.submissions), 1)
        mission_id, work_unit_id, submission = verifier.submissions[0]
        self.assertEqual((mission_id, work_unit_id), ("mis-desktop-1", "wu-desktop-1"))
        self.assertEqual(submission.verdict, EvidenceVerdict.PASS)
        self.assertEqual(submission.criterion_id, "desktop-artifacts")
        self.assertEqual(submission.verifier_id, DESKTOP_VERIFIER_ID)
        self.assertEqual(
            submission.configuration_digest,
            "sha256:" + "b" * 64,
        )
        self.assertEqual(
            [(ref.id, ref.digest) for ref in submission.artifact_refs],
            [("art-1", digest)],
        )
        self.assertIn("verified 1 Artifact", submission.summary)

    async def test_missing_artifact_bytes_submits_fail_with_reason(self) -> None:
        digest = "sha256:" + hashlib.sha256(b"nowhere").hexdigest()
        verifier = FakeVerifierControl(
            ready_discovery(digest, 12, f"local:sha256/{digest.removeprefix('sha256:')}")
        )
        controller = await self._run_controller(verifier)
        try:
            await asyncio.wait_for(verifier.submitted.wait(), timeout=2)
        finally:
            await controller.stop()

        _mission_id, _work_unit_id, submission = verifier.submissions[0]
        self.assertEqual(submission.verdict, EvidenceVerdict.FAIL)
        self.assertIn("unavailable", submission.summary)

    async def test_empty_artifact_submits_fail_without_byte_read(self) -> None:
        digest = "sha256:" + "c" * 64
        verifier = FakeVerifierControl(
            ready_discovery(digest, 0, f"local:sha256/{'c' * 64}")
        )
        controller = await self._run_controller(verifier)
        try:
            await asyncio.wait_for(verifier.submitted.wait(), timeout=2)
        finally:
            await controller.stop()

        _mission_id, _work_unit_id, submission = verifier.submissions[0]
        self.assertEqual(submission.verdict, EvidenceVerdict.FAIL)
        self.assertIn("empty", submission.summary)

    async def test_idle_discovery_submits_nothing(self) -> None:
        verifier = FakeVerifierControl(None)
        controller = await self._run_controller(verifier)
        try:
            await asyncio.sleep(0.05)
        finally:
            await controller.stop()
        self.assertEqual(verifier.submissions, [])
        self.assertGreaterEqual(len(verifier.discover_calls), 1)

    async def test_inconclusive_policy_submits_nothing(self) -> None:
        digest, size, address = self._store_artifact(b"manual output")
        manual_policy = {
            "status": "inconclusive",
            "reasonCode": "no_applicable_policy",
            "criterionIds": ["desktop-review"],
        }
        verifier = FakeVerifierControl(
            ready_discovery(digest, size, address, policy=manual_policy)
        )
        controller = await self._run_controller(verifier)
        try:
            await asyncio.sleep(0.05)
        finally:
            await controller.stop()
        self.assertEqual(verifier.submissions, [])

    async def test_verify_disabled_spawns_no_loop(self) -> None:
        digest, size, address = self._store_artifact(b"never verified")
        verifier = FakeVerifierControl(ready_discovery(digest, size, address))
        controller = DesktopLocalRunnerController(
            desktop_settings(verify_enabled=False),
            control=_IdleControl(),
            publisher=FakePublisher(),
            model_factory=StaticModelFactory(ScriptedModel()),
            mission_source=IdleMissionSource(),
            workspace_root=self.workspace_root,
            verifier_control=verifier,
            byte_verifier=self._byte_verifier(),
        )
        await controller.start()
        try:
            self.assertIsNone(controller._verification_task)
            await asyncio.sleep(0.05)
            self.assertEqual(verifier.discover_calls, [])
        finally:
            await controller.stop()

    # ── Test-loop tasks: VERIFY: acceptance commands (P1) ──────────────

    async def _submit_for_objective(
        self,
        objective: str,
        **setting_overrides: Any,
    ) -> tuple[FakeVerifierControl, DesktopLocalRunnerController]:
        digest, size, address = self._store_artifact(b"task output")
        objective_lines = objective + "\nVERIFY: python check.py"
        verifier = FakeVerifierControl(
            ready_discovery(digest, size, address, objective=objective_lines)
        )
        controller = await self._run_controller(verifier, **setting_overrides)
        try:
            await asyncio.wait_for(verifier.submitted.wait(), timeout=10)
        finally:
            await controller.stop()
        return verifier, controller

    async def test_passing_verify_command_submits_pass_evidence(self) -> None:
        (self.workspace_root / "check.py").write_text(
            "print('OK')\n", encoding="utf-8"
        )
        verifier, _controller = await self._submit_for_objective(
            "修复后运行 python check.py 必须输出 OK"
        )

        _mission_id, _work_unit_id, submission = verifier.submissions[0]
        self.assertEqual(submission.verdict, EvidenceVerdict.PASS)
        self.assertIn("verified 1 Artifact", submission.summary)
        self.assertIn("Verify command(s) passed: python check.py", submission.summary)

    async def test_failing_verify_command_submits_fail_with_output_tail(
        self,
    ) -> None:
        (self.workspace_root / "check.py").write_text(
            "raise AssertionError('expected 80.0, got 120.0')\n",
            encoding="utf-8",
        )
        verifier, _controller = await self._submit_for_objective(
            "修复 calc.py 的折扣计算"
        )

        _mission_id, _work_unit_id, submission = verifier.submissions[0]
        self.assertEqual(submission.verdict, EvidenceVerdict.FAIL)
        self.assertIn("verify command failed with exit code 1", submission.summary)
        self.assertIn("expected 80.0, got 120.0", submission.summary)

    async def test_timed_out_verify_command_submits_fail(self) -> None:
        (self.workspace_root / "check.py").write_text(
            "import time; time.sleep(30)\n",
            encoding="utf-8",
        )
        verifier, _controller = await self._submit_for_objective(
            "修复慢任务",
            verify_command_timeout_seconds=0.5,
        )

        _mission_id, _work_unit_id, submission = verifier.submissions[0]
        self.assertEqual(submission.verdict, EvidenceVerdict.FAIL)
        self.assertIn("verify command timed out", submission.summary)

    async def test_verify_command_runs_only_after_artifact_facts_pass(self) -> None:
        digest = "sha256:" + hashlib.sha256(b"nowhere").hexdigest()
        missing_address = f"local:sha256/{digest.removeprefix('sha256:')}"
        verifier = FakeVerifierControl(
            ready_discovery(
                digest,
                12,
                missing_address,
                objective="修复任务\nVERIFY: python check.py",
            )
        )
        (self.workspace_root / "check.py").write_text(
            "raise AssertionError('must not run')\n",
            encoding="utf-8",
        )
        controller = await self._run_controller(verifier)
        try:
            await asyncio.wait_for(verifier.submitted.wait(), timeout=5)
        finally:
            await controller.stop()

        _mission_id, _work_unit_id, submission = verifier.submissions[0]
        self.assertEqual(submission.verdict, EvidenceVerdict.FAIL)
        self.assertIn("unavailable", submission.summary)
        self.assertNotIn("must not run", submission.summary)

    # ── Test-loop tasks: RUN: shell commands in acceptance (P1-3) ──────

    async def _submit_run_objective(
        self,
        script_body: str,
        **setting_overrides: Any,
    ) -> FakeVerifierControl:
        digest, size, address = self._store_artifact(b"task output")
        (self.workspace_root / "run_check.py").write_text(
            script_body, encoding="utf-8"
        )
        objective = "整理数据\nRUN: python run_check.py"
        verifier = FakeVerifierControl(
            ready_discovery(digest, size, address, objective=objective)
        )
        controller = await self._run_controller(verifier, **setting_overrides)
        try:
            await asyncio.wait_for(verifier.submitted.wait(), timeout=10)
        finally:
            await controller.stop()
        return verifier

    async def test_passing_run_command_executes_and_passes(self) -> None:
        verifier = await self._submit_run_objective("print('RUN OK')\n")

        _mission_id, _work_unit_id, submission = verifier.submissions[0]
        self.assertEqual(submission.verdict, EvidenceVerdict.PASS)
        self.assertIn("Run command(s) passed: python run_check.py", submission.summary)

    async def test_failing_run_command_submits_fail_with_output(self) -> None:
        verifier = await self._submit_run_objective(
            "raise AssertionError('RUN failed: bad data')\n"
        )

        _mission_id, _work_unit_id, submission = verifier.submissions[0]
        self.assertEqual(submission.verdict, EvidenceVerdict.FAIL)
        self.assertIn("Run command failed with exit code 1", submission.summary)
        self.assertIn("RUN failed: bad data", submission.summary)


class DesktopVerifyCommandUnitTests(DesktopWorkspaceTestCase):
    async def test_run_verify_command_returns_exit_code_and_output(self) -> None:
        outcome = await run_verify_command(
            'python -c "print(\'hello verify\')"',
            cwd=self.workspace_root,
            timeout_seconds=30,
        )
        self.assertEqual(outcome.exit_code, 0)
        self.assertFalse(outcome.timed_out)
        self.assertIn("hello verify", outcome.output)

    async def test_run_verify_command_captures_failure_output(self) -> None:
        outcome = await run_verify_command(
            'python -c "import sys; print(\'boom\'); sys.exit(3)"',
            cwd=self.workspace_root,
            timeout_seconds=30,
        )
        self.assertEqual(outcome.exit_code, 3)
        self.assertFalse(outcome.timed_out)
        self.assertIn("boom", outcome.output)

    async def test_run_verify_command_reports_timeout(self) -> None:
        outcome = await run_verify_command(
            'python -c "import time; time.sleep(30)"',
            cwd=self.workspace_root,
            timeout_seconds=0.5,
        )
        self.assertIsNone(outcome.exit_code)
        self.assertTrue(outcome.timed_out)

    def test_extract_verify_commands_reads_marker_lines(self) -> None:
        objective = (
            "修复 calc.py 的折扣计算。\n"
            "修复后运行 python check.py 必须输出 OK。\n"
            "VERIFY: python check.py\n"
            "忽略：VERIFY 不是标记\n"
            "VERIFY:   \n"
            "  VERIFY: python -m pytest -q\n"
        )
        self.assertEqual(
            extract_verify_commands(objective),
            ("python check.py", "python -m pytest -q"),
        )

    def test_extract_verify_commands_without_marker_is_empty(self) -> None:
        self.assertEqual(extract_verify_commands("普通任务描述\n没有标记"), ())

    def test_extract_run_commands_reads_marker_lines(self) -> None:
        objective = (
            "整理数据文件。\n"
            "RUN: python build.py\n"
            "  RUN:python check.py --quick\n"
            "RUN:   \n"
            "这不是标记: RUN: python x.py\n"
        )
        self.assertEqual(
            extract_run_commands(objective),
            ("python build.py", "python check.py --quick"),
        )

    def test_extract_run_commands_without_marker_is_empty(self) -> None:
        self.assertEqual(extract_run_commands("普通任务描述"), ())


# ── SQLite claim dialect (desktop local profile) ─────────────────────────


class DesktopSqliteClaimTests(unittest.IsolatedAsyncioTestCase):
    """Claim-path coverage against a real SQLite control plane.

    The desktop local profile runs Mission Control on SQLite; the claim
    candidate SQL, admission lock and tenant quota must work there without
    PostgreSQL-only constructs.
    """

    async def asyncSetUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pool = SQLitePool(Path(tmp.name) / "agenthub.db")
        await pool.initialize()
        self.addCleanup(pool.close)
        acquire = pool.acquire()
        self._conn = await acquire.__aenter__()

        async def _release() -> None:
            await acquire.__aexit__(None, None, None)

        self.addCleanup(_release)
        await _create_mission_control_plane_sqlite(self._conn)

        @asynccontextmanager
        async def _transaction_factory():
            yield self._conn

        self._repository = MissionRepository(
            execute=self._conn.execute,
            fetch_one=self._conn.fetchrow,
            fetch_all=self._conn.fetch,
            transaction_factory=_transaction_factory,
        )

    async def _seed_desktop_mission(self, mission_id: str) -> None:
        from tests.domain.factories import build_contract

        await self._repository.add_contract(
            build_contract(id="contract-desktop-1")
        )
        await self._repository.add_mission(
            build_mission(
                id=mission_id,
                workspace_id=WORKSPACE_ID,
                contract_id="contract-desktop-1",
                contract_version=1,
                status="RUNNING",
                source=MissionSource(type=MissionSourceType.MANUAL),
                created_by=ActorRef(type=ActorType.HUMAN, id=WORKSPACE_ID),
            )
        )

    async def _seed_desktop_task_unit(
        self,
        mission_id: str,
        unit_id: str = "wu-desktop-1",
        **updates: Any,
    ) -> None:
        values: dict[str, Any] = {
            "id": unit_id,
            "mission_id": mission_id,
            "kind": "desktop.task",
            "dependencies": [],
            "input_refs": [],
            "expected_outputs": [{"kind": "text", "required": False}],
            "required_capabilities": [],
            "assigned_agent_id": DESKTOP_AGENT_ID,
            "assigned_adapter": DESKTOP_ADAPTER_TYPE,
            "status": "PENDING",
            "attempt": 0,
        }
        values.update(updates)
        await self._repository.add_work_unit(build_work_unit(**values))

    async def _claim(self) -> Any:
        service = MissionService(self._repository)
        return await service.claim_workspace_bound_work_unit(
            WORKSPACE_ID,
            agent_id=DESKTOP_AGENT_ID,
            adapter_type=DESKTOP_ADAPTER_TYPE,
            supported_work_unit_kinds=("desktop.task",),
            runner_id=RUNNER_USER_ID,
            actor=ActorRef(type=ActorType.SERVICE, id=DESKTOP_RUNNER_LABEL),
            lease_seconds=300,
            admission_policy=WorkspaceClaimAdmissionPolicy(
                tenant_id=WORKSPACE_ID,
                max_concurrent=0,
            ),
        )

    async def test_empty_dependency_pending_unit_is_claimable(self) -> None:
        await self._seed_desktop_mission("mis-claim-ok")
        await self._seed_desktop_task_unit("mis-claim-ok")

        outcome = await self._claim()

        self.assertEqual(outcome.status, WorkspaceClaimStatus.CLAIMED)
        self.assertIsNotNone(outcome.work_unit)
        self.assertEqual(outcome.work_unit.id, "wu-desktop-1")
        self.assertEqual(outcome.work_unit.status, WorkUnitStatus.LEASED)
        self.assertIsNotNone(outcome.work_unit.lease)
        self.assertEqual(outcome.work_unit.lease.runner_id, RUNNER_USER_ID)

    async def test_expired_lease_unit_is_not_claimed(self) -> None:
        await self._seed_desktop_mission("mis-claim-stale")
        await self._seed_desktop_task_unit("mis-claim-stale")
        stale_lease = {
            "id": "lease-stale",
            "runnerId": "runner-ghost",
            "expiresAt": (
                datetime.now(timezone.utc) - timedelta(seconds=60)
            ).isoformat(),
        }
        await self._conn.execute(
            "UPDATE work_units SET lease=$2 WHERE id=$1",
            "wu-desktop-1",
            json.dumps(stale_lease),
        )

        outcome = await self._claim()

        self.assertEqual(outcome.status, WorkspaceClaimStatus.IDLE)
        self.assertIsNone(outcome.work_unit)
        row = await self._conn.fetchrow(
            "SELECT status, lease FROM work_units WHERE id=$1", "wu-desktop-1"
        )
        self.assertEqual(row["status"], "PENDING")
        self.assertIn("runner-ghost", row["lease"])

    async def test_unsatisfied_dependency_unit_is_not_claimed(self) -> None:
        await self._seed_desktop_mission("mis-claim-deps")
        await self._seed_desktop_task_unit(
            "mis-claim-deps", dependencies=["wu-dep-missing"]
        )

        outcome = await self._claim()

        self.assertEqual(outcome.status, WorkspaceClaimStatus.IDLE)
        self.assertIsNone(outcome.work_unit)

    async def test_sqlite_admission_lock_and_active_lease_count(self) -> None:
        await self._seed_desktop_mission("mis-admission")
        await self._seed_desktop_task_unit("mis-admission")
        await self._repository.lock_tenant_claim_admission(WORKSPACE_ID)
        self.assertEqual(
            await self._repository.count_tenant_active_runner_work_units(
                WORKSPACE_ID
            ),
            0,
        )

        outcome = await self._claim()
        self.assertEqual(outcome.status, WorkspaceClaimStatus.CLAIMED)
        self.assertEqual(
            await self._repository.count_tenant_active_runner_work_units(
                WORKSPACE_ID
            ),
            1,
        )

    async def test_sqlite_admission_resolver_returns_local_unlimited_policy(
        self,
    ) -> None:
        from app.db import session as db_session

        with (
            patch.object(db_session, "DB_BACKEND", "sqlite"),
            patch.object(db_session, "DATABASE_URL", ""),
        ):
            resolver = DatabaseWorkspaceClaimAdmissionPolicyResolver()
            policy = await resolver.resolve(workspace_id=WORKSPACE_ID)

        self.assertEqual(policy.tenant_id, WORKSPACE_ID)
        self.assertEqual(policy.max_concurrent, 0)


# ── P1-2: memory deposition after verifier PASS ──────────────────────────


class RecordingMemorySink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.deposited = asyncio.Event()

    async def save_mission_summary(
        self,
        mission_id: str,
        *,
        objective: str,
        summary: str,
    ) -> bool:
        self.calls.append((mission_id, objective, summary))
        self.deposited.set()
        return True


class DesktopMissionMemoryDepositionTests(DesktopWorkspaceTestCase):
    """PASS verdict deposits one mission memory; FAIL never does."""

    def _artifact_root(self) -> Path:
        root = Path(self._tmp.name) / "artifacts"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _byte_verifier(self) -> ContentAddressedArtifactByteVerifier:
        settings = SimpleNamespace(
            local_root=self._artifact_root(),
            verify_max_bytes=1024 * 1024,
        )
        return ContentAddressedArtifactByteVerifier(settings)

    def _store_artifact(self, content: bytes) -> tuple[str, int, str]:
        digest = hashlib.sha256(content).hexdigest()
        sha_dir = self._artifact_root() / "sha256"
        sha_dir.mkdir(parents=True, exist_ok=True)
        (sha_dir / digest).write_bytes(content)
        return f"sha256:{digest}", len(content), f"local:sha256/{digest}"

    async def _run_verification(
        self,
        sink: RecordingMemorySink,
        *,
        artifact_content: bytes | None,
        wait_deposit: bool = False,
    ) -> FakeVerifierControl:
        if artifact_content is not None:
            digest, size, address = self._store_artifact(artifact_content)
            discovery = ready_discovery(
                digest, size, address, objective="在工作区创建 hello.txt"
            )
        else:
            digest = "sha256:" + hashlib.sha256(b"nowhere").hexdigest()
            discovery = ready_discovery(
                digest,
                12,
                f"local:sha256/{digest.removeprefix('sha256:')}",
                objective="在工作区创建 hello.txt",
            )
        verifier = FakeVerifierControl(discovery)
        controller = DesktopLocalRunnerController(
            desktop_settings(verify_enabled=True),
            control=_IdleControl(),
            publisher=FakePublisher(),
            model_factory=StaticModelFactory(ScriptedModel()),
            mission_source=IdleMissionSource(),
            workspace_root=self.workspace_root,
            verifier_control=verifier,
            byte_verifier=self._byte_verifier(),
            memory_sink=sink,
        )
        await controller.start()
        try:
            await asyncio.wait_for(verifier.submitted.wait(), timeout=5)
            if wait_deposit:
                await asyncio.wait_for(sink.deposited.wait(), timeout=5)
            else:
                # Give the loop a beat to finish (or skip) the deposition.
                await asyncio.sleep(0.1)
        finally:
            await controller.stop()
        return verifier

    async def test_pass_evidence_deposits_mission_memory(self) -> None:
        sink = RecordingMemorySink()
        summary_text = "已创建 hello.txt，内容为问候语。"
        verifier = await self._run_verification(
            sink,
            artifact_content=summary_text.encode("utf-8"),
            wait_deposit=True,
        )

        _mission_id, _work_unit_id, submission = verifier.submissions[0]
        self.assertEqual(submission.verdict, EvidenceVerdict.PASS)
        self.assertEqual(len(sink.calls), 1)
        mission_id, objective, summary = sink.calls[0]
        self.assertEqual(mission_id, "mis-desktop-1")
        self.assertEqual(objective, "在工作区创建 hello.txt")
        self.assertIn("已创建 hello.txt", summary)

    async def test_fail_evidence_skips_memory_deposition(self) -> None:
        sink = RecordingMemorySink()
        await self._run_verification(sink, artifact_content=None)
        self.assertEqual(sink.calls, [])


# ── P2-2: parallel RunnerWorkers ─────────────────────────────────────────


class TwoUnitControl:
    """Serves two claimable units to whichever worker claims first."""

    def __init__(
        self,
        units: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> None:
        self._queue = list(units)
        self._contexts = {
            claimed["workUnit"]["id"]: context for claimed, context in units
        }
        self.claimed_by: dict[str, str] = {}
        self.completed: list[str] = []
        self.failures: list[tuple[str, str]] = []
        self.statuses: list[str] = []
        self._lock = asyncio.Lock()
        self.all_done = asyncio.Event()

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
        del workspace_id, agent_id, adapter_type, lease_seconds
        assert "desktop.task" in supported_work_unit_kinds
        async with self._lock:
            if not self._queue:
                return {"claimStatus": "idle", "workUnit": None}
            claimed, _context = self._queue.pop(0)
            work_unit_id = claimed["workUnit"]["id"]
            # The control plane stamps the lease with the claiming runner.
            claimed["workUnit"]["lease"]["runnerId"] = runner_id
            self.claimed_by[work_unit_id] = runner_id
            self.statuses.append("LEASED")
            return copy.deepcopy(claimed)

    async def get_execution_context(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
    ) -> dict[str, Any]:
        del mission_id, lease_id
        context = copy.deepcopy(self._contexts[work_unit_id])
        context["workUnit"]["lease"]["runnerId"] = runner_id
        return {"executionContext": context}

    async def start_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
    ) -> dict[str, Any]:
        del mission_id, work_unit_id, runner_id
        self.statuses.append("RUNNING")
        return {"lease": {"id": lease_id}, "attempt": 1}

    async def heartbeat_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        del mission_id, work_unit_id, runner_id, lease_seconds
        return {"lease": {"id": lease_id}, "attempt": 1}

    async def record_execution_checkpoint(
        self,
        mission_id: str,
        work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
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
        del mission_id, work_unit_id, runner_id, lease_id, artifact, kind, media_type
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
        del mission_id, runner_id, lease_id, artifact_refs
        self.statuses.append("SUCCEEDED")
        self.completed.append(work_unit_id)
        if len(self.completed) == 2:
            self.all_done.set()
        return {}

    async def fail_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        lease_id: str,
        reason: str,
    ) -> dict[str, Any]:
        del mission_id, runner_id, lease_id
        self.failures.append((work_unit_id, reason))
        return {"lease": {"id": lease_id}, "attempt": 1}


class SlowScriptedModel(ScriptedModel):
    """ScriptedModel whose first turn yields control long enough for the
    second worker to poll and claim the other unit."""

    async def complete(self, request, tool_results):
        await asyncio.sleep(0.05)
        return await super().complete(request, tool_results)


async def _noop_auto_git_commit(*args: Any, **kwargs: Any) -> None:
    del args, kwargs


def multi_worker_claim_payloads() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Two independent desktop.task claims on two manual Missions."""
    units: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index in (1, 2):
        lease = Lease(
            id=f"lease-multi-{index}",
            runner_id=RUNNER_USER_ID,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
        )
        work_unit = WorkUnit(
            id=f"wu-multi-{index}",
            mission_id=f"mis-multi-{index}",
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
            id=f"mis-multi-{index}",
            workspace_id=WORKSPACE_ID,
            title=f"并行任务 {index}",
            objective=f"在工作区创建 hello-{index}.txt，内容为 hello {index}",
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
        units.append((claimed, context))
    return units


class DesktopMultiWorkerTests(DesktopWorkspaceTestCase):
    """P2-2: ``AGENTHUB_DESKTOP_LOCAL_RUNNER_WORKERS`` parallel claim loop."""

    def _controller(self, control: TwoUnitControl) -> DesktopLocalRunnerController:
        return DesktopLocalRunnerController(
            desktop_settings(workers=2),
            control=control,
            publisher=FakePublisher(),
            model_factory=StaticModelFactory(SlowScriptedModel()),
            mission_source=IdleMissionSource(),
            workspace_root=self.workspace_root,
        )

    async def test_two_workers_claim_different_units_in_parallel(self) -> None:
        control = TwoUnitControl(multi_worker_claim_payloads())
        controller = self._controller(control)
        # The fire-and-forget git auto-commit after file_write races with
        # test workspace cleanup on Windows; it is not part of this contract.
        with patch(
            "app.services.tools.builtin_tools._auto_git_commit",
            new=_noop_auto_git_commit,
        ):
            await controller.start()
            try:
                self.assertEqual(len(controller._workers), 2)
                await asyncio.wait_for(control.all_done.wait(), timeout=10)
            finally:
                await controller.stop()

        self.assertEqual(
            sorted(control.completed), ["wu-multi-1", "wu-multi-2"]
        )
        self.assertEqual(control.failures, [])
        # Two distinct workers claimed the two units: runner ids carry the
        # worker sequence suffix.
        runner_ids = set(control.claimed_by.values())
        self.assertEqual(len(runner_ids), 2)
        self.assertTrue(
            all(runner_id.endswith(("-w0", "-w1")) for runner_id in runner_ids)
        )
        self.assertTrue((self.workspace_root / "hello.txt").exists())
        self.assertEqual(
            (self.workspace_root / "hello.txt").read_text(encoding="utf-8"),
            "hello desktop runner",
        )

    async def test_stop_stops_every_worker(self) -> None:
        control = TwoUnitControl(multi_worker_claim_payloads())
        controller = self._controller(control)
        with patch(
            "app.services.tools.builtin_tools._auto_git_commit",
            new=_noop_auto_git_commit,
        ):
            await controller.start()
            self.assertEqual(len(controller._worker_tasks), 2)
            await controller.stop()
        self.assertEqual(controller._worker_tasks, [])
        self.assertEqual(controller._workers, [])


if __name__ == "__main__":
    unittest.main()
