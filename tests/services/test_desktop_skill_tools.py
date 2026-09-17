"""Tests for the read-only workspace skill tools (north-star M1).

Covers: whitelist presence, SKILL.md discovery, frontmatter parsing
through the shared parser, traversal-shaped name rejection, and honest
failure when a skill is missing.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services.desktop_runner_tools import (
    SKILL_LIST_TOOL_NAME,
    SKILL_LOAD_TOOL_NAME,
    WEB_SEARCH_ENV,
    _validate_skill_load_arguments,
    build_desktop_runner_tools,
)

_SKILL_MD = """---
name: pdf-report
description: 生成 PDF 报告的流程
version: 1.2.0
---

1. 先用 file_read 读取数据源
2. 生成 markdown 中间稿
3. 校验后输出最终文件
"""


def _make_workspace() -> Path:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    skills = root / ".claude" / "skills" / "pdf-report"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    # A directory without SKILL.md must be skipped, not crash.
    (root / ".claude" / "skills" / "broken").mkdir()
    return root


class SkillToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        skills = self.workspace / ".claude" / "skills" / "pdf-report"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
        (self.workspace / ".claude" / "skills" / "broken").mkdir()

    def _tools(self) -> dict[str, object]:
        with mock.patch.dict(os.environ, {WEB_SEARCH_ENV: "0"}):
            tools = build_desktop_runner_tools(self.workspace)
        return {tool.name: tool for tool in tools}

    def test_skill_tools_in_whitelist(self) -> None:
        tools = self._tools()
        self.assertIn(SKILL_LIST_TOOL_NAME, tools)
        self.assertIn(SKILL_LOAD_TOOL_NAME, tools)

    def test_skill_list_reports_workspace_skills(self) -> None:
        tool = self._tools()[SKILL_LIST_TOOL_NAME]
        rendered = asyncio.run(tool.handler({}))
        self.assertIn("pdf-report", rendered)
        self.assertIn("生成 PDF 报告", rendered)
        # The broken directory is skipped silently.
        self.assertNotIn("broken", rendered)

    def test_skill_load_returns_body(self) -> None:
        tool = self._tools()[SKILL_LOAD_TOOL_NAME]
        rendered = asyncio.run(tool.handler({"name": "pdf-report"}))
        self.assertIn("1.2.0", rendered)
        self.assertIn("markdown 中间稿", rendered)

    def test_skill_load_missing_skill_fails_honestly(self) -> None:
        tool = self._tools()[SKILL_LOAD_TOOL_NAME]
        rendered = asyncio.run(tool.handler({"name": "no-such-skill"}))
        self.assertIn("工具执行失败", rendered)
        self.assertIn("no-such-skill", rendered)

    def test_skill_list_empty_workspace(self) -> None:
        empty = self.workspace / "empty-ws"
        empty.mkdir()
        with mock.patch.dict(os.environ, {WEB_SEARCH_ENV: "0"}):
            tools = build_desktop_runner_tools(empty)
        by_name = {tool.name: tool for tool in tools}
        rendered = asyncio.run(by_name[SKILL_LIST_TOOL_NAME].handler({}))
        self.assertIn("没有技能包", rendered)


class SkillValidationTests(unittest.TestCase):
    def test_name_required(self) -> None:
        with self.assertRaises(ValueError):
            _validate_skill_load_arguments({"name": "  "})

    def test_traversal_rejected(self) -> None:
        for bad in ("../secrets", "a/b", "a\\b", "..", "."):
            with self.assertRaises(ValueError, msg=bad):
                _validate_skill_load_arguments({"name": bad})

    def test_plain_name_accepted(self) -> None:
        self.assertEqual(
            _validate_skill_load_arguments({"name": " pdf-report "}),
            {"name": "pdf-report"},
        )


if __name__ == "__main__":
    unittest.main()
