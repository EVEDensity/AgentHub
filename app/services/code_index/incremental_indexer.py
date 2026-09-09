"""Incrementally maintain the optional workspace code index."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.services.code_index.index_store import CodeIndexStore
from app.services.code_index.symbol_extractor import extract_symbols, language_for_path

_IGNORED_DIRECTORIES = {".git", ".agenthub", ".venv", "node_modules", "__pycache__", "dist", "build", "target"}


@dataclass(frozen=True)
class IndexReport:
    indexed: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: int = 0
    used_git_diff: bool = False


class IncrementalIndexer:
    """Hash-check supported source files and update only changed records."""

    def __init__(self, workspace_root: Path, store: CodeIndexStore, *, max_file_bytes: int = 1_000_000) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.store = store
        self.max_file_bytes = max_file_bytes

    def update(self, changed_paths: Iterable[str | Path] | None = None, *, max_files: int | None = None) -> IndexReport:
        paths, used_git_diff = self._resolve_paths(changed_paths)
        indexed = unchanged = removed = failed = 0
        live_paths: set[str] = set()
        for path in paths:
            if max_files is not None and indexed + unchanged + failed >= max_files:
                break
            try:
                relative = path.resolve().relative_to(self.workspace_root).as_posix()
                live_paths.add(relative)
                if not path.exists():
                    if self.store.file_hash(relative) is not None:
                        self.store.remove_file(relative)
                        removed = 1
                    continue
                if not path.is_file() or path.stat().st_size > self.max_file_bytes:
                    continue
                digest = _sha256(path)
                if self.store.file_hash(relative) == digest:
                    unchanged += 1
                    continue
                result = extract_symbols(path)
                if result.parse_error:
                    failed += 1
                    continue
                self.store.replace_file(relative, digest, result)
                indexed += 1
            except (OSError, ValueError):
                failed += 1
        if changed_paths is None:
            for stale in self.store.indexed_paths() - live_paths:
                self.store.remove_file(stale)
                removed += 1
        return IndexReport(indexed, unchanged, removed, failed, used_git_diff)

    def _resolve_paths(self, changed_paths: Iterable[str | Path] | None) -> tuple[list[Path], bool]:
        if changed_paths is not None:
            return [self.workspace_root / Path(path) for path in changed_paths], False
        # A new/empty index must establish a complete baseline. Git status is
        # only an incremental hint after at least one file has been indexed.
        if not self.store.indexed_paths():
            return self._workspace_paths(), False
        changed = self._git_changed_paths()
        if changed:
            return [self.workspace_root / item for item in changed], True
        return self._workspace_paths(), False

    def _workspace_paths(self) -> list[Path]:
        paths: list[Path] = []
        for path in self.workspace_root.rglob("*"):
            try:
                relative_parts = path.relative_to(self.workspace_root).parts
            except ValueError:
                continue
            if any(part in _IGNORED_DIRECTORIES or part.startswith(".") for part in relative_parts):
                continue
            if path.is_file() and language_for_path(path) is not None:
                paths.append(path)
        return paths

    def _git_changed_paths(self) -> list[Path]:
        try:
            command = ["git", "-C", str(self.workspace_root), "status", "--porcelain", "--untracked-files=all"]
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            return []
        if completed.returncode != 0:
            return []
        changed: list[Path] = []
        for line in completed.stdout.splitlines():
            if len(line) < 4:
                continue
            value = line[3:].split(" -> ")[-1].strip()
            path = Path(value)
            if language_for_path(path) is not None:
                changed.append(path)
        return changed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
