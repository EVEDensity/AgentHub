"""Regression tests for project-root resolution.

Historical bug: ``_PROJECT_ROOT`` escaped one level above the checkout
(``_BASE_DIR.parent``), so ``.claude`` memory/skill state was addressed
outside the workspace boundary. The default must now be the checkout
directory itself, with ``AGENTHUB_PROJECT_ROOT`` as the explicit override
for packaged layouts.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from app.core import config as config_module


class ProjectRootTests(unittest.TestCase):
    def test_default_is_checkout_dir(self) -> None:
        # Re-resolve without the override in the environment.
        with mock.patch.dict(os.environ, {"AGENTHUB_PROJECT_ROOT": ""}):
            root = config_module._resolve_project_root()
        self.assertEqual(root, config_module._BASE_DIR)

    def test_memory_dir_stays_inside_checkout(self) -> None:
        with mock.patch.dict(os.environ, {"AGENTHUB_PROJECT_ROOT": ""}):
            root = config_module._resolve_project_root()
        memory_dir = root / ".claude" / "memory"
        self.assertEqual(
            memory_dir, config_module._BASE_DIR / ".claude" / "memory"
        )
        self.assertNotEqual(root, config_module._BASE_DIR.parent)

    def test_explicit_override_wins(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {"AGENTHUB_PROJECT_ROOT": str(Path("/custom/install/root"))},
            )
        ):
            root = config_module._resolve_project_root()
        self.assertEqual(root, Path("/custom/install/root").resolve())


if __name__ == "__main__":
    unittest.main()
