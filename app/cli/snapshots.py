"""Attempt-scoped workspace snapshots with conflict-safe restoration."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    return proc.stdout if proc.returncode == 0 else ""


def _hash(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


@dataclass(frozen=True)
class AttemptSnapshot:
    id: str
    root: Path
    store: Path
    baseline: dict[str, str | None]
    baseline_status: frozenset[str]
    post: dict[str, str | None] | None = None

    @property
    def metadata_path(self) -> Path:
        return self.store / "snapshot.json"

    def finalize(self) -> "AttemptSnapshot":
        status_paths = {
            line[3:].strip().split(" -> ")[-1]
            for line in _git(self.root, "status", "--short").splitlines()
            if len(line) > 3
        }
        paths = set(self.baseline) | status_paths
        post = {path: _hash(self.root / path) for path in paths if path}
        payload = {"id": self.id, "baseline": self.baseline, "baselineStatus": sorted(self.baseline_status), "post": post}
        self.store.mkdir(parents=True, exist_ok=True)
        self.metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return AttemptSnapshot(self.id, self.root, self.store, self.baseline, self.baseline_status, post)

    def restore(self) -> tuple[bool, list[str]]:
        if self.post is None:
            return False, ["snapshot is not finalized"]
        conflicts: list[str] = []
        for path, before in self.baseline.items():
            current = _hash(self.root / path)
            after = self.post.get(path)
            if current == before:
                continue
            if current != after:
                conflicts.append(path)
                continue
            target = self.store / "files" / path
            if before is None:
                try:
                    (self.root / path).unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    conflicts.append(path)
            elif target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, self.root / path)
            else:
                proc = subprocess.run(["git", "-C", str(self.root), "restore", "--worktree", "--", path], capture_output=True, timeout=10)
                if proc.returncode != 0:
                    conflicts.append(path)
        return not conflicts, conflicts


def capture_attempt(root: Path, store_root: Path) -> AttemptSnapshot:
    root = root.resolve()
    status_lines = _git(root, "status", "--short").splitlines()
    status_paths: set[str] = set()
    untracked: set[str] = set()
    for line in status_lines:
        if len(line) > 3:
            path = line[3:].strip().split(" -> ")[-1]
            status_paths.add(path)
            if line.startswith("??"):
                untracked.add(path)
    tracked = [line.strip() for line in _git(root, "ls-files").splitlines() if line.strip()]
    paths = set(tracked) | status_paths
    baseline = {path: (None if path in untracked else _hash(root / path)) for path in paths}
    snapshot_id = "att-" + uuid.uuid4().hex
    snapshot = AttemptSnapshot(snapshot_id, root, store_root / snapshot_id, baseline, frozenset(status_paths))
    snapshot.store.mkdir(parents=True, exist_ok=True)
    for path, digest in baseline.items():
        if digest is not None and path in status_paths:
            source = root / path
            if source.is_file():
                target = snapshot.store / "files" / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    return snapshot


def load_snapshot(root: Path, store: Path, snapshot_id: str) -> AttemptSnapshot | None:
    path = store / snapshot_id / "snapshot.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return AttemptSnapshot(snapshot_id, root.resolve(), store / snapshot_id, dict(payload["baseline"]), frozenset(payload.get("baselineStatus", [])), dict(payload.get("post", {})))
    except (OSError, ValueError, KeyError, TypeError):
        return None


__all__ = ["AttemptSnapshot", "capture_attempt", "load_snapshot"]
