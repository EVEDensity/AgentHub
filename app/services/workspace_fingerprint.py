"""Content-free workspace and context fingerprints shared by CLI and Runner."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def workspace_revision(root: Path) -> str:
    """Return a deterministic revision from Git state or file metadata."""
    root = Path(root).resolve()
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout
        material = head + "\n" + status
    except (OSError, subprocess.SubprocessError):
        entries: list[str] = []
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_file() and ".git" not in path.parts and ".agenthub" not in path.parts:
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    entries.append(f"{path.relative_to(root).as_posix()}:{stat.st_size}:{stat.st_mtime_ns}")
        material = "\n".join(entries)
    return "sha256:" + hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()


def context_manifest_digest(text: str) -> str:
    """Hash compiled model context without persisting its contents."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


__all__ = ["context_manifest_digest", "workspace_revision"]
