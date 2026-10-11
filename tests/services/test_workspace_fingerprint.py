from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import workspace_fingerprint as fingerprints


@pytest.fixture
def git_workspace(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("real Git fingerprint tests require Git")
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".gitignore").write_text("ignored.txt\ncache/\n", encoding="utf-8")
    (root / "tracked.txt").write_text("original", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", ".gitignore", "tracked.txt"], check=True)
    subprocess.run([
        "git", "-C", str(root), "-c", "user.name=Fingerprint Test",
        "-c", "user.email=fingerprint@example.test", "commit", "-qm", "fixture",
    ], check=True)
    return root


def test_real_git_ignored_file_edits_are_fingerprinted(git_workspace: Path) -> None:
    ignored = git_workspace / "ignored.txt"
    ignored.write_text("first", encoding="utf-8")
    first = fingerprints.workspace_revision(git_workspace)
    ignored.write_text("second", encoding="utf-8")
    assert fingerprints.workspace_revision(git_workspace) != first


def test_real_git_ignored_directory_add_delete_changes_revision(git_workspace: Path) -> None:
    before = fingerprints.workspace_revision(git_workspace)
    cache = git_workspace / "cache"
    cache.mkdir()
    empty = fingerprints.workspace_revision(git_workspace)
    assert empty != before
    file = cache / "output.bin"
    file.write_bytes(b"real output\x00")
    populated = fingerprints.workspace_revision(git_workspace)
    assert populated != empty
    file.unlink()
    assert fingerprints.workspace_revision(git_workspace) == empty
    cache.rmdir()
    assert fingerprints.workspace_revision(git_workspace) == before


def test_already_dirty_same_size_edit_with_preserved_mtime_is_detected(git_workspace: Path) -> None:
    path = git_workspace / "tracked.txt"
    path.write_bytes(b"first---")
    first = fingerprints.workspace_revision(git_workspace)
    before = path.stat()
    path.write_bytes(b"second--")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert fingerprints.workspace_revision(git_workspace) != first


@pytest.mark.parametrize("directory", [".agenthub", ".tmp", "node_modules", "__pycache__", ".next", ".venv", "target"])
def test_writable_runtime_and_cache_directories_are_not_silently_excluded(tmp_path: Path, directory: str) -> None:
    root = tmp_path / "workspace"
    path = root / directory / "writable.data"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"first")
    before = fingerprints.workspace_revision(root)
    path.write_bytes(b"second")
    assert fingerprints.workspace_revision(root) != before


def test_git_administrative_file_does_not_change_workspace_revision(git_workspace: Path) -> None:
    before = fingerprints.workspace_revision(git_workspace)
    path = git_workspace / ".git" / "audit-administration"
    path.write_bytes(b"private git housekeeping")
    assert fingerprints.workspace_revision(git_workspace) == before


def test_same_bytes_and_paths_have_stable_revision_despite_mtime_changes(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"stable bytes")
    before = fingerprints.workspace_revision(tmp_path)
    current = path.stat()
    os.utime(path, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000_000))
    assert fingerprints.workspace_revision(tmp_path) == before


def test_binary_contents_cannot_mimic_additional_path_entries(tmp_path: Path) -> None:
    path = tmp_path / "a"
    path.write_bytes(b"X\x00b\x00Y")
    before = fingerprints.workspace_revision(tmp_path)
    path.write_bytes(b"X")
    (tmp_path / "b").write_bytes(b"Y")
    assert fingerprints.workspace_revision(tmp_path) != before


def test_directory_link_is_hashed_without_following_external_content(tmp_path: Path) -> None:
    root, outside = tmp_path / "workspace", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    external = outside / "private.txt"
    external.write_text("first secret", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation unavailable: {type(exc).__name__}")
    before = fingerprints.workspace_revision(root)
    external.write_text("second secret", encoding="utf-8")
    assert fingerprints.workspace_revision(root) == before
    link.unlink()
    assert fingerprints.workspace_revision(root) != before


def test_unreadable_file_cannot_be_omitted_from_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "unreadable"
    path.write_bytes(b"must be hashed")
    original = Path.open

    def deny_file(self: Path, *args: object, **kwargs: object):
        if self == path:
            raise PermissionError("test prevents reading one workspace file")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_file)
    with pytest.raises(PermissionError):
        fingerprints.workspace_revision(tmp_path)


def test_path_added_during_fingerprinting_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "initial"
    path.write_bytes(b"initial")
    original = fingerprints._entry_digest

    def add_path(value: Path) -> bytes:
        result = original(value)
        (tmp_path / "concurrent").write_bytes(b"new entry")
        return result

    monkeypatch.setattr(fingerprints, "_entry_digest", add_path)
    with pytest.raises(fingerprints.WorkspaceFingerprintError, match="paths changed"):
        fingerprints.workspace_revision(tmp_path)
