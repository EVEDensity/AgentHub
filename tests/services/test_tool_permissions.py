"""Tests for Codex-style tool permission tiers (north-star I-6b).

suggest = read-only + research; edit = + file writes (historical
default); auto = full whitelist. Denied tools keep their schema but
return an actionable denial message instead of executing.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from unittest import mock

from app.services.desktop_runner_tools import (
    TOOL_PERMISSION_AUTO,
    TOOL_PERMISSION_EDIT,
    TOOL_PERMISSION_MODES,
    TOOL_PERMISSION_SUGGEST,
    WEB_FETCH_TOOL_NAME,
    build_desktop_runner_tools,
    resolve_tool_permission_mode,
)
from app.cli.runtime import build_server_env, execute_objective


def _tool_map(mode: str | None = None) -> dict[str, object]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tools = build_desktop_runner_tools(Path(tmp), permission_mode=mode)
    return {t.name: t for t in tools}


def _deny_map() -> dict[str, object]:
    return _tool_map(TOOL_PERMISSION_SUGGEST)


class ResolveModeTests(unittest.TestCase):
    def test_explicit_argument_wins(self) -> None:
        with mock.patch.dict(os.environ, {TOOL_PERMISSION_MODES and "AGENTHUB_TOOL_PERMISSION_MODE": "auto"}):
            self.assertEqual(resolve_tool_permission_mode("suggest"), "suggest")

    def test_env_fallback(self) -> None:
        with mock.patch.dict(os.environ, {"AGENTHUB_TOOL_PERMISSION_MODE": "suggest"}):
            self.assertEqual(resolve_tool_permission_mode(None), "suggest")

    def test_default_is_edit(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_tool_permission_mode(None), TOOL_PERMISSION_EDIT)

    def test_unknown_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_tool_permission_mode("yolo")

    def test_env_is_case_insensitive(self) -> None:
        with mock.patch.dict(os.environ, {"AGENTHUB_TOOL_PERMISSION_MODE": "Suggest"}):
            self.assertEqual(resolve_tool_permission_mode(None), "suggest")


class TierShapeTests(unittest.TestCase):
    """Same toolset shape at every tier — only handlers differ."""

    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _names(self, mode: str | None) -> set[str]:
        return {t.name for t in build_desktop_runner_tools(self.root, permission_mode=mode)}

    def test_tiers_expose_identical_tool_names(self) -> None:
        suggest = self._names("suggest")
        edit = self._names("edit")
        auto = self._names("auto")
        self.assertEqual(suggest, edit)
        self.assertEqual(edit, auto)

    def test_default_matches_edit(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self._names(None), self._names("edit"))


class SuggestDenialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = _deny_map()

    def _call(self, name: str, arguments: dict) -> str:
        handler = self.tools[name].handler
        return asyncio.run(handler(arguments))

    def test_file_write_denied(self) -> None:
        output = self._call("file_write", {"path": "x.txt", "content": "x"})
        self.assertIn("权限档位", output)
        self.assertIn("suggest", output)

    def test_file_edit_denied(self) -> None:
        output = self._call(
            "file_edit", {"path": "x.txt", "old_text": "a", "new_text": "b"}
        )
        self.assertIn("权限档位", output)

    def test_code_execute_denied(self) -> None:
        output = self._call("code_execute", {"code": "print(1)"})
        self.assertIn("权限档位", output)

    def test_read_tools_still_work(self) -> None:
        # Read-side tools are not stubbed: file_read on a missing file
        # must produce the real (non-permission) error path.
        output = self._call("file_read", {"path": "no-such.txt"})
        self.assertNotIn("权限档位", output)

    def test_denial_mentions_upgrade_path(self) -> None:
        output = self._call("mkdir", {"path": "newdir"})
        self.assertIn("更高权限档位", output)


class EditTierTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.tools = {
            t.name: t
            for t in build_desktop_runner_tools(
                self.root, permission_mode=TOOL_PERMISSION_EDIT
            )
        }

    def test_file_write_executes(self) -> None:
        handler = self.tools["file_write"].handler
        output = asyncio.run(handler({"path": "ok.txt", "content": "data", "expected_sha256": ""}))
        self.assertNotIn("权限档位", output)
        self.assertEqual((self.root / "ok.txt").read_text(encoding="utf-8"), "data")


class AutoTierTests(unittest.TestCase):
    def test_code_execute_runs(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = {
                t.name: t
                for t in build_desktop_runner_tools(
                    root, permission_mode=TOOL_PERMISSION_AUTO
                )
            }
        output = asyncio.run(tools["code_execute"].handler({"code": "print(2+2)"}))
        self.assertNotIn("权限档位", output)


class ServerEnvTests(unittest.TestCase):
    def test_permission_mode_travels_to_subprocess(self) -> None:
        from app.cli.runtime import CliModelSettings

        env = build_server_env(
            db_path=Path("db/x.db"),
            data_dir=Path("data"),
            workspace_root=Path("."),
            port=28100,
            model=CliModelSettings(
                provider="mock", model="m", api_key="k", base_url=""
            ),
            max_total_tokens=1000,
            runner_timeout_seconds=60.0,
            web_search=False,
            tool_permission_mode="suggest",
        )
        self.assertEqual(env.get("AGENTHUB_TOOL_PERMISSION_MODE"), "suggest")

    def test_absent_mode_resolves_canonical_edit_policy(self) -> None:
        from app.cli.runtime import CliModelSettings

        env = build_server_env(
            db_path=Path("db/x.db"),
            data_dir=Path("data"),
            workspace_root=Path("."),
            port=28100,
            model=CliModelSettings(
                provider="mock", model="m", api_key="k", base_url=""
            ),
            max_total_tokens=1000,
            runner_timeout_seconds=60.0,
            web_search=False,
        )
        self.assertEqual(env["AGENTHUB_TOOL_PERMISSION_MODE"], "edit")


class ParserTests(unittest.TestCase):
    def test_run_accepts_permission_flag(self) -> None:
        from app.cli.main import build_parser

        args = build_parser().parse_args(
            ["run", "do it", "--permission", "suggest"]
        )
        self.assertEqual(args.permission, "suggest")

    def test_exec_permission_default_none(self) -> None:
        from app.cli.main import build_parser

        args = build_parser().parse_args(["exec", "do it", "--json"])
        self.assertIsNone(args.permission)

    def test_invalid_permission_rejected(self) -> None:
        from app.cli.main import build_parser

        with self.assertRaises(SystemExit):
            build_parser().parse_args(["run", "do it", "--permission", "yolo"])


if __name__ == "__main__":
    unittest.main()
