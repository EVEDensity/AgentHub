from __future__ import annotations

import json
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
