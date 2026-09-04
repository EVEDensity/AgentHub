from pathlib import Path

from app.cli.snapshots import capture_attempt


def test_attempt_snapshot_restores_preexisting_change(tmp_path: Path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    (tmp_path / "a.txt").write_text("user", encoding="utf-8")
    snap = capture_attempt(tmp_path, tmp_path / ".snapshots")
    (tmp_path / "a.txt").write_text("agent", encoding="utf-8")
    snap = snap.finalize()
    ok, conflicts = snap.restore()
    assert ok and not conflicts
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "user"


def test_attempt_snapshot_rejects_external_conflict(tmp_path: Path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    snap = capture_attempt(tmp_path, tmp_path / ".snapshots")
    (tmp_path / "a.txt").write_text("agent", encoding="utf-8")
    snap = snap.finalize()
    (tmp_path / "a.txt").write_text("external", encoding="utf-8")
    ok, conflicts = snap.restore()
    assert not ok and conflicts == ["a.txt"]
