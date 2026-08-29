"""DATA_DIR / WORKSPACES_DIR env-override coverage (desktop local profile)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import (
    _BASE_DIR,
    _resolve_data_dir,
    _resolve_workspaces_dir,
)


class ResolveDataDirTests(unittest.TestCase):
    def test_without_env_data_dir_stays_in_project(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            self.assertEqual(_resolve_data_dir(), _BASE_DIR / "data")

    def test_agenthub_local_data_redirects_data_dir(self) -> None:
        with patch.dict(
            "os.environ", {"AGENTHUB_LOCAL_DATA": r"D:\AgentHub"}, clear=False
        ):
            self.assertEqual(_resolve_data_dir(), Path(r"D:\AgentHub") / "data")

    def test_blank_agenthub_local_data_is_ignored(self) -> None:
        with patch.dict("os.environ", {"AGENTHUB_LOCAL_DATA": "   "}, clear=False):
            self.assertEqual(_resolve_data_dir(), _BASE_DIR / "data")


class ResolveWorkspacesDirTests(unittest.TestCase):
    def test_explicit_workspaces_dir_wins_over_local_data(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "AGENTHUB_WORKSPACES_DIR": r"E:\custom-workspaces",
                "AGENTHUB_LOCAL_DATA": r"D:\AgentHub",
            },
            clear=False,
        ):
            self.assertEqual(_resolve_workspaces_dir(), Path(r"E:\custom-workspaces"))

    def test_agenthub_local_data_derives_workspaces_subdir(self) -> None:
        with patch.dict(
            "os.environ", {"AGENTHUB_LOCAL_DATA": r"D:\AgentHub"}, clear=False
        ):
            self.assertEqual(
                _resolve_workspaces_dir(), Path(r"D:\AgentHub") / "data" / "workspaces"
            )

    def test_without_env_workspaces_dir_stays_in_project(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            self.assertEqual(
                _resolve_workspaces_dir(), _BASE_DIR / "data" / "workspaces"
            )


if __name__ == "__main__":
    unittest.main()
