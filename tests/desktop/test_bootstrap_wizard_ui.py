"""Structural smoke tests for the desktop first-run bootstrap wizard.

The wizard (``desktop/ui/index.html`` + ``main.js`` + ``styles.css``) is
plain JavaScript inside the Tauri webview; there is no JS test runner in
the desktop package. These tests pin the wiring contract between the UI
and the Rust ``bootstrap_stack`` command / ``bootstrap-progress`` channel
(north-star M3 / section 4.0):

- the wizard dialog and its controls exist with stable element ids,
- ``main.js`` invokes ``bootstrap_stack`` with a manifest URL,
- progress events from the ``bootstrap-progress`` channel are rendered,
- failures surface a retry path (resume) without touching the pinned stack,
- skipping is session-scoped and the wizard stays reachable from settings.
"""

from __future__ import annotations

import unittest
from pathlib import Path

DESKTOP_UI = Path(__file__).resolve().parents[2] / "desktop" / "ui"

REQUIRED_DIALOG_IDS = (
    "bootstrap-dialog",
    "bootstrap-form",
    "bootstrap-manifest-url",
    "bootstrap-progress",
    "bootstrap-bar-fill",
    "bootstrap-progress-text",
    "bootstrap-log",
    "bootstrap-error",
    "bootstrap-skip",
    "bootstrap-retry",
    "bootstrap-start",
)


def _read(name: str) -> str:
    path = DESKTOP_UI / name
    if not path.is_file():
        raise AssertionError(f"desktop UI file is missing: {path}")
    return path.read_text(encoding="utf-8")


class BootstrapWizardHtmlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = _read("index.html")

    def test_dialog_contains_all_controls(self) -> None:
        for element_id in REQUIRED_DIALOG_IDS:
            self.assertIn(
                f'id="{element_id}"',
                self.html,
                f"wizard control #{element_id} is missing from index.html",
            )

    def test_manifest_url_input_is_required_http_url(self) -> None:
        self.assertIn('id="bootstrap-manifest-url" type="url" required', self.html)

    def test_settings_entry_point_exists(self) -> None:
        self.assertIn('id="open-bootstrap-wizard"', self.html)

    def test_default_manifest_url_points_at_release_source(self) -> None:
        # The placeholder shows users where the official release manifest
        # lives; it must match the publish-stack job's asset layout.
        self.assertIn(
            "releases/latest/download/stack-manifest.json",
            self.html,
        )


class BootstrapWizardJsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.js = _read("main.js")

    def test_invokes_bootstrap_stack_with_manifest_url(self) -> None:
        self.assertIn("bootstrap_stack", self.js)
        self.assertIn("{ manifestUrl }", self.js)

    def test_listens_to_bootstrap_progress_channel(self) -> None:
        self.assertIn("'bootstrap-progress'", self.js)
        self.assertIn("renderBootstrapProgress", self.js)

    def test_progress_renders_sha256_verified_files(self) -> None:
        # Each verified/downloaded file is reported per-file with an index
        # and total so the progress bar can be computed client-side.
        self.assertIn("payload.index", self.js)
        self.assertIn("payload.total", self.js)
        self.assertIn("payload.path", self.js)

    def test_failure_shows_retry_with_resume_semantics(self) -> None:
        # A failed download must surface the error and offer retry; the
        # copy must tell the user verified files are kept (resume) and the
        # current pin is untouched (atomic switch).
        self.assertIn("bootstrapRetry", self.js)
        self.assertIn("断点续传", self.js)

    def test_first_run_detection_requires_stack_info(self) -> None:
        # The wizard only auto-opens when no stack manifest exists and no
        # persisted stack is cached on the machine.
        self.assertIn("maybeShowFirstRunWizard", self.js)
        self.assertIn("stack_info", self.js)

    def test_skip_is_session_scoped(self) -> None:
        # Skipping sets a session flag so the wizard does not nag within
        # the same session, while remaining available from settings.
        self.assertIn("sessionStorage", self.js)

    def test_browser_preview_fixture_for_bootstrap(self) -> None:
        # Outside the Tauri shell (design preview in a plain browser) the
        # command fixture must resolve so the dialog stays demoable.
        self.assertIn("'bootstrap_stack'", self.js)

    def test_remembered_manifest_url(self) -> None:
        # The last used manifest URL is persisted so upgrades reuse it.
        self.assertIn("agenthub-bootstrap-manifest-url", self.js)


class BootstrapWizardStyleTests(unittest.TestCase):
    def test_wizard_styles_exist(self) -> None:
        css = _read("styles.css")
        for selector in (".bootstrap-bar", ".bootstrap-bar-fill", ".bootstrap-log", ".bootstrap-error"):
            self.assertIn(selector, css, f"{selector} styles are missing")


if __name__ == "__main__":
    unittest.main()
