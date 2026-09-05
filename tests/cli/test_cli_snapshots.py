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
    preview_ok, preview_conflicts = snap.preview_restore()
    assert not preview_ok and preview_conflicts == ["a.txt"]
    ok, conflicts = snap.restore()
    assert not ok and conflicts == ["a.txt"]


def test_attempt_snapshot_restores_index_and_removes_new_file(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    snap = capture_attempt(tmp_path, tmp_path / ".snapshots")
    (tmp_path / "a.txt").write_text("agent", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"], check=True)
    (tmp_path / "new.txt").write_text("new", encoding="utf-8")
    snap = snap.finalize()
    ok, conflicts = snap.restore()
    assert ok and not conflicts
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "base"
    assert not (tmp_path / "new.txt").exists()
    assert subprocess.run(["git", "-C", str(tmp_path), "diff", "--cached", "--exit-code"], capture_output=True).returncode == 0


def test_restore_is_atomic_when_index_has_external_change(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    snap = capture_attempt(tmp_path, tmp_path / ".snapshots")
    (tmp_path / "a.txt").write_text("agent", encoding="utf-8")
    snap = snap.finalize()
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"], check=True)
    (tmp_path / "a.txt").write_text("external", encoding="utf-8")
    ok, conflicts = snap.restore()
    assert not ok and "a.txt" in conflicts
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "external"


def test_attempt_snapshot_restores_file_touched_by_multiple_work_units(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "shared.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "shared.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    snap = capture_attempt(tmp_path, tmp_path / ".snapshots")
    # WorkUnit A and B both touch the same path; the attempt snapshot owns
    # the aggregate and restores the single pre-attempt version.
    (tmp_path / "shared.txt").write_text("unit-a", encoding="utf-8")
    (tmp_path / "shared.txt").write_text("unit-b-final", encoding="utf-8")
    snap = snap.finalize()
    ok, conflicts = snap.restore()
    assert ok and not conflicts
    assert (tmp_path / "shared.txt").read_text(encoding="utf-8") == "base"


def test_attempt_snapshot_writes_review_manifest(tmp_path: Path):
    import json, subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    snap = capture_attempt(tmp_path, tmp_path / ".snapshots")
    (tmp_path / "a.txt").write_text("changed", encoding="utf-8")
    snap = snap.finalize()
    path = snap.write_manifest(work_units=[{"id": "wu-1", "status": "verified", "changedFiles": ["a.txt"]}], artifacts=[{"id": "art-1", "kind": "patch", "workUnitId": "wu-1", "sha256": "abc"}])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert payload["changedFiles"] == ["a.txt"]
    assert payload["workUnits"][0]["id"] == "wu-1"
    assert payload["workUnits"][0]["changedFiles"] == ["a.txt"]
    assert payload["artifacts"][0]["workUnitId"] == "wu-1"
