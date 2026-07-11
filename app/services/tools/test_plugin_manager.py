# ─────────────────────────────────────────────────────────────────────
# Tests for PluginManager + HookManager dual-track integration (Sprint 5)
# ─────────────────────────────────────────────────────────────────────
# Run: pytest app/services/tools/test_plugin_manager.py -v
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from app.services.tools.plugin_spec import hookimpl, ToolHookSpecs
from app.services.tools.plugin_manager import PluginManager, plugin_manager


# ── Test fixtures ──────────────────────────────────────────────────────


class _BlockingPlugin:
    """Test plugin that blocks all tool calls."""

    @hookimpl
    def pre_tool_use(self, tool_name, arguments, context):
        return {"blocked": True, "reason": "blocked by test"}

    @hookimpl
    def tool_categories(self):
        return None


class _ModifyingPlugin:
    """Test plugin that modifies input and result."""

    @hookimpl
    def pre_tool_use(self, tool_name, arguments, context):
        return {"modified_input": {**arguments, "injected": True}}

    @hookimpl
    def post_tool_use(self, tool_name, arguments, result, context):
        return {"modified_result": {**result, "sanitized": True}}

    @hookimpl
    def tool_categories(self):
        return None


class _RegisterToolsPlugin:
    """Test plugin that registers custom tools."""

    @hookimpl
    def register_tools(self):
        return [
            {
                "name": "custom_tool",
                "description": "a test tool",
                "category": "test",
                "parameters": {"type": "object", "properties": {}},
            }
        ]

    @hookimpl
    def tool_categories(self):
        return ["test"]


class _NoopPlugin:
    """Test plugin that does nothing (returns None for all hooks)."""

    @hookimpl
    def pre_tool_use(self, tool_name, arguments, context):
        return None

    @hookimpl
    def post_tool_use(self, tool_name, arguments, result, context):
        return None


# ── 1. PluginManager initialization ───────────────────────────────────


class TestPluginManagerInit:
    def test_plugin_manager_singleton_exists(self):
        assert plugin_manager is not None
        assert isinstance(plugin_manager, PluginManager)

    def test_hookspec_registered(self):
        pm = PluginManager()
        # The hook relay should have our hookspec methods
        assert hasattr(pm.hook, "pre_tool_use")
        assert hasattr(pm.hook, "post_tool_use")
        assert hasattr(pm.hook, "register_tools")
        assert hasattr(pm.hook, "tool_categories")

    def test_list_plugins_empty_initially(self):
        pm = PluginManager()
        assert pm.list_plugins() == {}

    def test_is_registered_false_for_unknown(self):
        pm = PluginManager()
        assert not pm.is_registered("nonexistent.plugin")


# ── 2. Builtin plugin loading ─────────────────────────────────────────


class TestBuiltinPlugins:
    def test_load_builtin_plugins_registers_three(self):
        pm = PluginManager()
        pm.load_builtin_plugins()
        plugins = pm.list_plugins()
        assert "builtin.audit" in plugins
        assert "builtin.permission" in plugins
        assert "builtin.sanitize" in plugins
        assert plugins["builtin.audit"] == "AuditPlugin"
        assert plugins["builtin.permission"] == "PermissionPlugin"
        assert plugins["builtin.sanitize"] == "SanitizePlugin"

    def test_load_builtin_plugins_idempotent(self):
        pm = PluginManager()
        pm.load_builtin_plugins()
        pm.load_builtin_plugins()  # second call should not raise
        plugins = pm.list_plugins()
        assert len(plugins) == 3

    def test_builtin_audit_plugin_logs_post_tool_use(self, caplog):
        import logging
        caplog.set_level(logging.INFO, logger="agenthub.plugins.audit")
        pm = PluginManager()
        pm.load_builtin_plugins()
        results = pm.hook.post_tool_use(
            tool_name="web_search",
            arguments={"query": "test"},
            result={"success": True, "output": "result text"},
            context={"user_id": "u1", "tenant_id": "t1"},
        )
        # AuditPlugin returns None (no modification)
        assert all(r is None for r in results)
        # But should have logged
        assert any("tool_audit" in rec.message for rec in caplog.records)

    def test_builtin_permission_plugin_allows_dev_mode(self):
        """Dev mode (no roles/scopes in context) → allow."""
        pm = PluginManager()
        pm.load_builtin_plugins()
        results = pm.hook.pre_tool_use(
            tool_name="code_execute",
            arguments={"code": "print(1)"},
            context={},  # no roles, no scopes → dev mode
        )
        # No blocking result
        assert not any(isinstance(r, dict) and r.get("blocked") for r in results)

    def test_builtin_permission_plugin_blocks_high_risk_for_member(self):
        """member role calling code_execute → blocked."""
        pm = PluginManager()
        pm.load_builtin_plugins()
        results = pm.hook.pre_tool_use(
            tool_name="code_execute",
            arguments={"code": "print(1)"},
            context={"roles": ["member"], "scopes": ["tool:execute"]},
        )
        blocked = [r for r in results if isinstance(r, dict) and r.get("blocked")]
        assert len(blocked) == 1
        assert "high-risk" in blocked[0]["reason"]

    def test_builtin_permission_plugin_allows_high_risk_for_operator(self):
        """agent_operator role calling code_execute → allowed."""
        pm = PluginManager()
        pm.load_builtin_plugins()
        results = pm.hook.pre_tool_use(
            tool_name="code_execute",
            arguments={"code": "print(1)"},
            context={"roles": ["agent_operator"], "scopes": []},
        )
        assert not any(isinstance(r, dict) and r.get("blocked") for r in results)

    def test_builtin_sanitize_plugin_redacts_secrets(self):
        """post_tool_use with AWS key in stdout → redacted."""
        pm = PluginManager()
        pm.load_builtin_plugins()
        results = pm.hook.post_tool_use(
            tool_name="code_execute",
            arguments={},
            result={"success": True, "stdout": "key=AKIAIOSFODNN7EXAMPLE"},
            context={"sanitize_level": "basic"},
        )
        modified = [r for r in results if isinstance(r, dict) and "modified_result" in r]
        assert len(modified) == 1
        assert "[REDACTED:AWS_KEY]" in modified[0]["modified_result"]["stdout"]

    def test_builtin_sanitize_plugin_off_level_no_modification(self):
        """sanitize_level=off → no modification."""
        pm = PluginManager()
        pm.load_builtin_plugins()
        results = pm.hook.post_tool_use(
            tool_name="code_execute",
            arguments={},
            result={"success": True, "stdout": "key=AKIAIOSFODNN7EXAMPLE"},
            context={"sanitize_level": "off"},
        )
        assert all(r is None for r in results)


# ── 3. Custom plugin registration & hook invocation ──────────────────


class TestCustomPlugins:
    def test_register_blocking_plugin(self):
        pm = PluginManager()
        pm.pm.register(_BlockingPlugin(), name="test.blocking")
        results = pm.hook.pre_tool_use(
            tool_name="any_tool",
            arguments={},
            context={},
        )
        blocked = [r for r in results if isinstance(r, dict) and r.get("blocked")]
        assert len(blocked) == 1
        assert blocked[0]["reason"] == "blocked by test"

    def test_register_modifying_plugin_pre(self):
        pm = PluginManager()
        pm.pm.register(_ModifyingPlugin(), name="test.modifying")
        results = pm.hook.pre_tool_use(
            tool_name="any_tool",
            arguments={"original": True},
            context={},
        )
        modified = [r for r in results if isinstance(r, dict) and "modified_input" in r]
        assert len(modified) == 1
        assert modified[0]["modified_input"]["original"] is True
        assert modified[0]["modified_input"]["injected"] is True

    def test_register_modifying_plugin_post(self):
        pm = PluginManager()
        pm.pm.register(_ModifyingPlugin(), name="test.modifying")
        results = pm.hook.post_tool_use(
            tool_name="any_tool",
            arguments={},
            result={"success": True},
            context={},
        )
        modified = [r for r in results if isinstance(r, dict) and "modified_result" in r]
        assert len(modified) == 1
        assert modified[0]["modified_result"]["success"] is True
        assert modified[0]["modified_result"]["sanitized"] is True

    def test_register_tools_plugin(self):
        pm = PluginManager()
        pm.pm.register(_RegisterToolsPlugin(), name="test.register")
        results = pm.hook.register_tools()
        # Returns list of lists (each plugin returns a list)
        all_tools: list[dict[str, Any]] = []
        for r in results or []:
            if isinstance(r, list):
                all_tools.extend(r)
        assert any(t["name"] == "custom_tool" for t in all_tools)

    def test_noop_plugin_returns_none(self):
        pm = PluginManager()
        pm.pm.register(_NoopPlugin(), name="test.noop")
        results = pm.hook.pre_tool_use(
            tool_name="any_tool",
            arguments={},
            context={},
        )
        assert all(r is None for r in results)

    def test_list_plugins_includes_custom(self):
        pm = PluginManager()
        pm.pm.register(_BlockingPlugin(), name="test.blocking")
        plugins = pm.list_plugins()
        assert "test.blocking" in plugins
        assert plugins["test.blocking"] == "_BlockingPlugin"


# ── 4. Path-based loading ─────────────────────────────────────────────


class TestPathLoading:
    def test_load_from_file(self, tmp_path: Path):
        """load_from_path loads a single .py file with a plugin class."""
        plugin_code = '''
from app.services.tools.plugin_spec import hookimpl

class PathPlugin:
    @hookimpl
    def pre_tool_use(self, tool_name, arguments, context):
        return {"blocked": True, "reason": "path plugin blocked"}
'''
        plugin_file = tmp_path / "path_plugin.py"
        plugin_file.write_text(plugin_code, encoding="utf-8")

        pm = PluginManager()
        count = pm.load_from_path(str(plugin_file))
        assert count == 1
        assert pm.is_registered("user.PathPlugin")

        results = pm.hook.pre_tool_use(
            tool_name="any_tool", arguments={}, context={},
        )
        blocked = [r for r in results if isinstance(r, dict) and r.get("blocked")]
        assert len(blocked) == 1
        assert blocked[0]["reason"] == "path plugin blocked"

    def test_load_from_directory(self, tmp_path: Path):
        """load_from_path loads all .py files in a directory."""
        (tmp_path / "a.py").write_text(
            "from app.services.tools.plugin_spec import hookimpl\n"
            "class PluginA:\n"
            "    @hookimpl\n"
            "    def pre_tool_use(self, tool_name, arguments, context):\n"
            "        return None\n",
            encoding="utf-8",
        )
        (tmp_path / "b.py").write_text(
            "from app.services.tools.plugin_spec import hookimpl\n"
            "class PluginB:\n"
            "    @hookimpl\n"
            "    def pre_tool_use(self, tool_name, arguments, context):\n"
            "        return None\n",
            encoding="utf-8",
        )
        pm = PluginManager()
        count = pm.load_from_path(str(tmp_path))
        assert count == 2
        assert pm.is_registered("user.PluginA")
        assert pm.is_registered("user.PluginB")

    def test_load_from_nonexistent_path(self):
        pm = PluginManager()
        count = pm.load_from_path("/nonexistent/path/plugin.py")
        assert count == 0

    def test_load_from_invalid_file(self, tmp_path: Path):
        """A .py file with no plugin classes → 0 loaded, no error."""
        (tmp_path / "empty.py").write_text(
            "# no plugin class here\nx = 42\n", encoding="utf-8",
        )
        pm = PluginManager()
        count = pm.load_from_path(str(tmp_path / "empty.py"))
        assert count == 0


# ── 5. load_all integration ───────────────────────────────────────────


class TestLoadAll:
    def test_load_all_idempotent(self):
        pm = PluginManager()
        pm.load_all()
        first = pm.list_plugins()
        pm.load_all()  # second call is no-op
        second = pm.list_plugins()
        assert first == second

    def test_load_all_loads_builtins(self):
        pm = PluginManager()
        pm.load_all()
        plugins = pm.list_plugins()
        assert "builtin.audit" in plugins
        assert "builtin.permission" in plugins
        assert "builtin.sanitize" in plugins


# ── 6. HookManager backward compatibility (dual-track) ────────────────


class TestHookManagerBackwardCompat:
    """Verify existing hook_manager.register_pre/register_post + run_* still work."""

    def test_register_pre_and_run_pre_hooks(self):
        """Legacy async hook registered via register_pre still fires."""
        from app.services.tools.hooks import HookManager

        hm = HookManager()
        called: list[str] = []

        async def my_hook(tool_name, arguments, context):
            called.append(tool_name)
            from app.services.tools.hooks import PreToolUseResult
            return PreToolUseResult()

        hm.register_pre("web_search", my_hook)
        result = asyncio.run(
            hm.run_pre_hooks("web_search", {"q": "test"}, {})
        )
        assert "web_search" in called
        assert not result.blocked

    def test_run_pre_hooks_blocked_by_legacy_async(self):
        """Legacy async hook can block."""
        from app.services.tools.hooks import HookManager, PreToolUseResult

        hm = HookManager()

        async def blocking_hook(tool_name, arguments, context):
            return PreToolUseResult(blocked=True, reason="legacy block")

        hm.register_pre("dangerous_tool", blocking_hook)
        result = asyncio.run(
            hm.run_pre_hooks("dangerous_tool", {}, {})
        )
        assert result.blocked
        assert result.reason == "legacy block"

    def test_run_pre_hooks_modified_input_legacy(self):
        """Legacy async hook can modify input."""
        from app.services.tools.hooks import HookManager, PreToolUseResult

        hm = HookManager()

        async def modifying_hook(tool_name, arguments, context):
            return PreToolUseResult(modified_input={**arguments, "added": True})

        hm.register_pre("some_tool", modifying_hook)
        result = asyncio.run(
            hm.run_pre_hooks("some_tool", {"original": 1}, {})
        )
        assert result.modified_input is not None
        assert result.modified_input["original"] == 1
        assert result.modified_input["added"] is True

    def test_get_hook_count(self):
        from app.services.tools.hooks import HookManager, PreToolUseResult, PostToolUseResult

        hm = HookManager()

        async def pre_hook(tool_name, arguments, context):
            return PreToolUseResult()

        async def post_hook(tool_name, arguments, result, context):
            return PostToolUseResult()

        hm.register_pre("tool_a", pre_hook)
        hm.register_pre("tool_b", pre_hook)
        hm.register_post(None, post_hook)

        counts = hm.get_hook_count()
        assert counts["pre"] == 2
        assert counts["post"] == 1

    def test_register_post_and_run_post_hooks(self):
        from app.services.tools.hooks import HookManager, PostToolUseResult

        hm = HookManager()
        called: list[str] = []

        async def my_post_hook(tool_name, arguments, result, context):
            called.append(tool_name)
            return PostToolUseResult(modified_result={**result, "post_processed": True})

        hm.register_post(None, my_post_hook)
        result = asyncio.run(
            hm.run_post_hooks("any_tool", {}, {"success": True}, {})
        )
        assert "any_tool" in called
        assert result.modified_result is not None
        assert result.modified_result["post_processed"] is True


# ── 7. Dual-track integration (pluggy + legacy) ──────────────────────


class TestDualTrackIntegration:
    """Verify pluggy and legacy async hooks run together correctly."""

    def test_pluggy_block_short_circuits_legacy(self):
        """When a pluggy plugin blocks, legacy async hooks do NOT run."""
        from app.services.tools.hooks import HookManager, PreToolUseResult

        hm = HookManager()
        legacy_called: list[str] = []

        # Register a blocking pluggy plugin
        hm._ensure_pluggy_loaded()
        if hm._pm is not None:
            hm._pm.pm.register(_BlockingPlugin(), name="test.dual.block")

        async def legacy_hook(tool_name, arguments, context):
            legacy_called.append(tool_name)
            return PreToolUseResult()

        hm.register_pre("any_tool", legacy_hook)
        result = asyncio.run(
            hm.run_pre_hooks("any_tool", {}, {})
        )
        assert result.blocked
        assert result.reason == "blocked by test"
        # Legacy hook should NOT have been called (short-circuited)
        assert "any_tool" not in legacy_called

    def test_pluggy_post_runs_after_legacy(self):
        """Both pluggy and legacy post hooks run; pluggy runs last."""
        from app.services.tools.hooks import HookManager, PostToolUseResult

        hm = HookManager()
        order: list[str] = []

        hm._ensure_pluggy_loaded()

        class OrderTrackingPlugin:
            @hookimpl
            def post_tool_use(self, tool_name, arguments, result, context):
                order.append("pluggy")
                return None

        if hm._pm is not None:
            hm._pm.pm.register(OrderTrackingPlugin(), name="test.order")

        async def legacy_post(tool_name, arguments, result, context):
            order.append("legacy")
            return PostToolUseResult(modified_result={**result, "legacy_done": True})

        hm.register_post(None, legacy_post)
        result = asyncio.run(
            hm.run_post_hooks("any_tool", {}, {"success": True}, {})
        )
        # Legacy runs first, pluggy runs after
        assert order == ["legacy", "pluggy"]
        # Legacy modification preserved
        assert result.modified_result is not None
        assert result.modified_result["legacy_done"] is True


# ── 8. Example plugin integration ─────────────────────────────────────


class TestExamplePlugin:
    """Verify the example_plugin in plugins/ loads and counts calls."""

    def test_example_plugin_loads_and_counts(self):
        pm = PluginManager()
        pm.load_builtin_plugins()
        # Load the example plugin from the project root
        example_path = Path(__file__).parent.parent.parent.parent / "plugins" / "example_plugin" / "plugin.py"
        if example_path.exists():
            count = pm.load_from_path(str(example_path))
            assert count == 1
            assert pm.is_registered("user.ExamplePlugin")

            # Invoke post_tool_use a few times
            for _ in range(3):
                pm.hook.post_tool_use(
                    tool_name="web_search",
                    arguments={},
                    result={"success": True},
                    context={},
                )

            # Inspect the plugin instance's counter
            plugin_instance = pm.pm.get_plugin("user.ExamplePlugin")
            assert plugin_instance is not None
            assert plugin_instance.counts.get("web_search") == 3
        else:
            pytest.skip(f"example plugin not found at {example_path}")
