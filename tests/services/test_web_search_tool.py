"""Tests for the public-web search tool (north-star M1).

Network access is mocked: these verify argument validation, backend
resolution, env gating, result clipping, and the honest-failure
contract (never a synthetic result set).
"""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from unittest import mock

from app.services.desktop_runner_tools import (
    WEB_SEARCH_ENV,
    WEB_SEARCH_TOOL_NAME,
    _validate_web_search_arguments,
    build_desktop_runner_tools,
    web_search_enabled,
)
from app.services.tools.network_tools import (
    WEB_SEARCH_MAX_RESULTS,
    _clip,
    web_search_handler,
)


class ValidationTests(unittest.TestCase):
    def test_query_required(self) -> None:
        with self.assertRaises(ValueError):
            _validate_web_search_arguments({"query": "   "})

    def test_max_results_must_be_number(self) -> None:
        with self.assertRaises(ValueError):
            _validate_web_search_arguments({"query": "q", "max_results": "five"})

    def test_valid_arguments_normalized(self) -> None:
        normalized = _validate_web_search_arguments(
            {"query": "  fastapi deps  ", "max_results": 3}
        )
        self.assertEqual(normalized["query"], "fastapi deps")
        self.assertEqual(normalized["max_results"], 3)


class GateTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        with mock.patch.dict(os.environ, {WEB_SEARCH_ENV: ""}):
            self.assertFalse(web_search_enabled())

    def test_enabled_values(self) -> None:
        for value in ("1", "true", "YES", "True"):
            with mock.patch.dict(os.environ, {WEB_SEARCH_ENV: value}):
                self.assertTrue(web_search_enabled(), value)
        with mock.patch.dict(os.environ, {WEB_SEARCH_ENV: "0"}):
            self.assertFalse(web_search_enabled())


class WhitelistTests(unittest.TestCase):
    def _tool_names(self, enabled: bool) -> list[str]:
        with mock.patch.dict(os.environ, {WEB_SEARCH_ENV: "1" if enabled else "0"}):
            tools = build_desktop_runner_tools(Path.cwd())
        return [tool.name for tool in tools]

    def test_web_search_in_whitelist_when_enabled(self) -> None:
        self.assertIn(WEB_SEARCH_TOOL_NAME, self._tool_names(True))

    def test_web_search_absent_when_disabled(self) -> None:
        self.assertNotIn(WEB_SEARCH_TOOL_NAME, self._tool_names(False))


class HandlerTests(unittest.TestCase):
    def test_empty_query_fails_honestly(self) -> None:
        outcome = asyncio.run(web_search_handler("   "))
        self.assertFalse(outcome["success"])
        self.assertIn("error", outcome)

    def test_max_results_clamped(self) -> None:
        # The clamp runs before any network call; mock the DDG backend to
        # capture the limit without real network access.
        async def fake_ddg(query: str, limit: int) -> dict:
            return {"success": True, "results": [], "limit": limit}

        with mock.patch(
            "app.services.tools.network_tools._ddg_html_search",
            side_effect=fake_ddg,
        ):
            outcome = asyncio.run(web_search_handler("q", max_results=999))
        self.assertEqual(outcome["limit"], WEB_SEARCH_MAX_RESULTS)

    def test_results_clipped(self) -> None:
        long_text = "x" * 5000
        clipped = _clip(long_text, 600)
        self.assertLessEqual(len(clipped), 601)
        self.assertTrue(clipped.endswith("…"))


if __name__ == "__main__":
    unittest.main()
