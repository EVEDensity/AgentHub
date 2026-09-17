from __future__ import annotations

import asyncio

from app.services.tools.project_tools import project_inspect_handler
from app.services.tools.git_tools import git_push_handler


def test_project_inspect_returns_read_only_manifest(tmp_path, monkeypatch) -> None:
    (tmp_path / "README.md").write_text("# Project\nSummary", encoding="utf-8")
    monkeypatch.setattr("app.services.workspace_context.get_workspace_root", lambda: tmp_path)

    result = asyncio.run(project_inspect_handler("."))

    assert result["success"] is True
    assert result["metadata"]["readOnly"] is True
    assert result["result"]["workspaceRoot"] == str(tmp_path.resolve())


def test_project_inspect_rejects_escape(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.services.workspace_context.get_workspace_root", lambda: tmp_path)

    result = asyncio.run(project_inspect_handler(".."))

    assert result["success"] is False


def test_git_push_is_explicitly_unsupported() -> None:
    result = asyncio.run(git_push_handler())

    assert result["success"] is False
    assert result["errorType"] == "unsupported_capability"
    assert result["metadata"]["requiresUserAction"] is True
