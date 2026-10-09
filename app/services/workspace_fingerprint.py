"""Content-free workspace and context fingerprints shared by CLI and Runner."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path


class WorkspaceFingerprintError(RuntimeError):
    """The complete workspace cannot be read as a trustworthy revision."""


def _is_link(path: Path) -> bool:
    junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(junction) and junction())


def _workspace_entries(root: Path) -> list[Path]:
    """Include ignored files and empty directories; never follow directory links."""
    pending = [root]
    paths: list[Path] = []
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name == ".git":
                    continue
                path = Path(entry.path)
                paths.append(path)
                if entry.is_dir(follow_symlinks=False) and not _is_link(path):
                    pending.append(path)
    return sorted(paths)


def _workspace_paths(root: Path) -> tuple[bytes, list[Path]]:
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, timeout=3, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        head = b"no-git"
    return head, _workspace_entries(root)


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    # Windows pathname stat and descriptor fstat can report different ctime
    # semantics. Compare ctime only between reads from the same descriptor.
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _entry_digest(path: Path) -> bytes:
    if _is_link(path):
        return b"link:" + hashlib.sha256(str(path.readlink()).encode("utf-8", "surrogateescape")).digest()
    before = path.stat(follow_symlinks=False)
    mode = stat.S_IMODE(before.st_mode).to_bytes(4, "big")
    if stat.S_ISDIR(before.st_mode):
        return b"directory:" + mode
    if not stat.S_ISREG(before.st_mode):
        raise WorkspaceFingerprintError("workspace contains an unsupported special file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if _stat_identity(opened) != _stat_identity(before):
            raise WorkspaceFingerprintError("workspace file changed during fingerprinting")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(handle.fileno())
    if _stat_identity(opened) != _stat_identity(after) or opened.st_ctime_ns != after.st_ctime_ns:
        raise WorkspaceFingerprintError("workspace file changed during fingerprinting")
    return b"file:" + mode + digest.digest()


def workspace_revision(root: Path) -> str:
    """Hash all writable workspace content, independent of Git ignore or metadata.

    Only Git administration is excluded. Runtime recovery databases belong
    outside this root; writable caches and project-local state are real input.
    File content digests are framed separately from names to avoid ambiguity
    when binary file contents contain path separators or NUL bytes.
    """
    root = Path(root).resolve(strict=True)
    head, paths = _workspace_paths(root)
    digest = hashlib.sha256(head)
    for path in paths:
        relative = path.relative_to(root)
        name = relative.as_posix().encode("utf-8", "surrogateescape")
        digest.update(len(name).to_bytes(8, "big") + name)
        entry = _entry_digest(path)
        digest.update(len(entry).to_bytes(8, "big") + entry)
    if paths != _workspace_entries(root):
        raise WorkspaceFingerprintError("workspace paths changed during fingerprinting")
    return "sha256:" + digest.hexdigest()


def context_manifest_digest(text: str) -> str:
    """Hash compiled model context without persisting its contents."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


__all__ = ["WorkspaceFingerprintError", "context_manifest_digest", "workspace_revision"]
