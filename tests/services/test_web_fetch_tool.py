"""Tests for the web_fetch browser-side tool (north-star §2 / I-6a).

web_fetch extends the web_search capability boundary: one public URL →
readable text, same SSRF rules as http_request, same desktop
gatekeeping switch, honest failures only.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

from app.services.desktop_runner_tools import (
    WEB_FETCH_TOOL_NAME,
    WEB_SEARCH_TOOL_NAME,
    _validate_web_fetch_arguments,
    build_desktop_runner_tools,
    web_search_enabled,
)
from app.services.tools import network_tools


class HtmlToTextTests(unittest.TestCase):
    def test_extracts_title_and_body(self) -> None:
        html = (
            "<html><head><title>Docs &amp; Guides</title>"
            "<style>body { color: red }</style></head>"
            "<body><h1>Intro</h1><p>Hello <b>world</b></p></body></html>"
        )
        title, text = network_tools._html_to_text(html)
        self.assertEqual(title, "Docs & Guides")
        self.assertIn("Intro", text)
        self.assertIn("Hello world", text)
        self.assertNotIn("color: red", text)

    def test_block_tags_separate_lines(self) -> None:
        html = "<p>one</p><p>two</p>"
        _, text = network_tools._html_to_text(html)
        self.assertEqual(text, "one\ntwo")

    def test_script_content_dropped(self) -> None:
        html = "<body><p>keep</p><script>var x = 'drop';</script></body>"
        _, text = network_tools._html_to_text(html)
        self.assertIn("keep", text)
        self.assertNotIn("drop", text)


class WebFetchHandlerTests(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_empty_url_fails_honestly(self) -> None:
        result = self._run(network_tools.web_fetch_handler(""))
        self.assertFalse(result["success"])

    def test_ssrf_blocked(self) -> None:
        for url in (
            "http://127.0.0.1:8000/admin",
            "http://localhost/x",
            "http://192.168.1.1/router",
            "http://internal.corp/",
            "ftp://example.com/file",
        ):
            result = self._run(network_tools.web_fetch_handler(url))
            self.assertFalse(result["success"], url)
            self.assertIn("error", result)

    def test_max_chars_clamped(self) -> None:
        # A max_chars above the ceiling clamps to WEB_FETCH_MAX_CHARS
        # and flags truncation for over-long bodies.
        response = mock.Mock()
        response.status_code = 200
        response.text = "y" * (network_tools.WEB_FETCH_MAX_CHARS + 5)
        response.headers = {"content-type": "text/plain"}
        response.url = "https://example.com/a"
        with mock.patch(
            "httpx.AsyncClient.get", new=mock.AsyncMock(return_value=response)
        ):
            result = self._run(
                network_tools.web_fetch_handler(
                    "https://example.com/a", max_chars=10**9
                )
            )
        self.assertTrue(result["success"])
        self.assertTrue(result["result"]["truncated"])
        self.assertEqual(
            len(result["result"]["content"]), network_tools.WEB_FETCH_MAX_CHARS
        )


class ValidationTests(unittest.TestCase):
    def test_requires_url(self) -> None:
        with self.assertRaises(ValueError):
            _validate_web_fetch_arguments({"url": "   "})

    def test_rejects_non_number_max_chars(self) -> None:
        with self.assertRaises(ValueError):
            _validate_web_fetch_arguments({"url": "https://a.com", "max_chars": "big"})

    def test_accepts_valid(self) -> None:
        args = _validate_web_fetch_arguments({"url": " https://a.com "})
        self.assertEqual(args["url"], "https://a.com")


class ToolRegistrationTests(unittest.TestCase):
    def _tool_names(self, enabled: bool) -> set[str]:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {"AGENTHUB_DESKTOP_WEB_SEARCH": "1" if enabled else "0"}
            ):
                tools = build_desktop_runner_tools(Path(tmp))
        return {t.name for t in tools}

    def test_enabled_exposes_both_web_tools(self) -> None:
        names = self._tool_names(True)
        self.assertIn(WEB_SEARCH_TOOL_NAME, names)
        self.assertIn(WEB_FETCH_TOOL_NAME, names)

    def test_disabled_exposes_neither(self) -> None:
        names = self._tool_names(False)
        self.assertNotIn(WEB_SEARCH_TOOL_NAME, names)
        self.assertNotIn(WEB_FETCH_TOOL_NAME, names)

    def test_gatekeeper_switch_untouched(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTHUB_DESKTOP_WEB_SEARCH", None)
            self.assertFalse(web_search_enabled())


class LiveFetchTests(unittest.TestCase):
    """One honest live round-trip (network-dependent, skipped offline)."""

    def test_fetch_public_page(self) -> None:
        try:
            result = asyncio.run(
                network_tools.web_fetch_handler("https://example.com")
            )
        except OSError:
            self.skipTest("no network")
        if not result.get("success"):
            # Rate limiting / offline: failure must still be the honest
            # error shape, never a synthetic document.
            self.assertIn("error", result)
            self.assertNotIn("result", result)
            return
        page = result["result"]
        self.assertEqual(page["kind"], "html")
        self.assertIn("Example Domain", page["title"] + page["content"])
        self.assertTrue(page["url"].startswith("https://example.com"))


if __name__ == "__main__":
    unittest.main()
