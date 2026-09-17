from __future__ import annotations

from types import SimpleNamespace

from scripts import verify_cli_release


def test_github_release_preflight_does_not_require_node_or_npm(monkeypatch, capsys):
    monkeypatch.setattr(
        verify_cli_release.shutil,
        "which",
        lambda name: "C:/Program Files/Git/bin/git.exe" if name == "git" else None,
    )
    monkeypatch.setattr(
        verify_cli_release.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    assert verify_cli_release.main() == 0
    output = capsys.readouterr().out
    assert '"git": true' in output
    assert '"npm"' not in output
    assert '"node"' not in output
