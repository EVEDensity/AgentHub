from __future__ import annotations

import json
import subprocess
import sys

from scripts import verify_github_release


def test_unsupported_platform_is_honest_skip(tmp_path, monkeypatch):
    output = tmp_path / "release.json"
    monkeypatch.setattr(verify_github_release.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sys, "argv", [
        "verify_github_release", "--target", "1.0.0",
        "--previous", "0.9.0", "--output", str(output),
    ])

    assert verify_github_release.main() == 0
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["status"] == "SKIP"
    assert record["scope"] == "github-release"


def test_installer_timeout_is_structured_failure(tmp_path, monkeypatch):
    output = tmp_path / "release.json"
    monkeypatch.setattr(verify_github_release.platform, "system", lambda: "Windows")
    monkeypatch.setattr(verify_github_release.shutil, "which", lambda _name: "powershell.exe")
    monkeypatch.setattr(
        verify_github_release,
        "_download_release_assets",
        lambda root, repository, version: (root / "archive.zip", root / "checksums.txt"),
    )
    monkeypatch.setattr(
        verify_github_release.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        ),
    )
    monkeypatch.setattr(sys, "argv", [
        "verify_github_release", "--target", "0.3.2",
        "--previous", "0.3.1", "--output", str(output),
    ])

    assert verify_github_release.main() == 1
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["errorType"] == "release_install_timeout"
    assert record["failedStep"] == "install_previous"


def test_incomplete_local_assets_fail_closed(tmp_path, monkeypatch):
    output = tmp_path / "release.json"
    archive = tmp_path / "previous.zip"
    archive.write_bytes(b"zip")
    monkeypatch.setattr(verify_github_release.platform, "system", lambda: "Windows")
    monkeypatch.setattr(verify_github_release.shutil, "which", lambda _name: "powershell.exe")
    monkeypatch.setattr(sys, "argv", [
        "verify_github_release", "--target", "0.3.3",
        "--previous", "0.3.2", "--previous-archive", str(archive),
        "--output", str(output),
    ])

    assert verify_github_release.main() == 1
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["errorType"] == "incomplete_local_assets"
