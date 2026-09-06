from __future__ import annotations

import json
import subprocess

from app.services.project_manifest import ProjectManifest


def test_discover_prefers_package_name_and_reports_stack(tmp_path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"name": "demo-app"}), encoding="utf-8")
    (tmp_path / "README.md").write_text("# Demo\nA useful project.\n", encoding="utf-8")
    manifest = ProjectManifest.discover(tmp_path)

    assert manifest.name == "demo-app"
    assert manifest.tech_stack == ("Node.js",)
    assert "A useful project." in manifest.readme_summary
    assert str(tmp_path.resolve()) in manifest.to_prompt()
    assert "AgentHub Developer CLI" in manifest.to_prompt()


def test_discover_uses_pyproject_and_redacts_remote(tmp_path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "py-demo"\n', encoding="utf-8")

    def fake_run(args, **kwargs):
        class Result:
            returncode = 0
            stdout = "https://user:secret@example.com/org/repo.git" if args[-2:] == ["--get", "remote.origin.url"] else "main"
        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    manifest = ProjectManifest.discover(tmp_path)

    assert manifest.name == "py-demo"
    assert manifest.git_branch == "main"
    assert manifest.git_remote == "https://example.com/org/repo.git"
    assert "secret" not in manifest.git_remote


def test_discover_falls_back_to_directory_name(tmp_path) -> None:
    assert ProjectManifest.discover(tmp_path).name == tmp_path.name
