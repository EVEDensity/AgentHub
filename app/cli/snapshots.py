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


def _index_entries(root: Path, paths: set[str]) -> dict[str, str]:
    if not paths:
        return {}
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-s", "--", *sorted(paths)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    entries: dict[str, str] = {}
    if proc.returncode != 0:
        return entries
    for line in proc.stdout.splitlines():
        if "\t" in line:
            meta, path = line.split("\t", 1)
            entries[path] = f"{meta}\t{path}"
    return entries


@dataclass(frozen=True)
class AttemptSnapshot:
    id: str
    root: Path
    store: Path
    baseline: dict[str, str | None]
    baseline_status: frozenset[str]
    baseline_index: dict[str, str]
    post: dict[str, str | None] | None = None
    post_index: dict[str, str] | None = None

    @property
    def metadata_path(self) -> Path:
        return self.store / "snapshot.json"

    @property
    def manifest_path(self) -> Path:
        return self.store / "manifest.json"

    def write_manifest(self, *, work_units: list[dict] | None = None, artifacts: list[dict] | None = None) -> Path:
        """Write content-minimized attempt metadata for review and replay."""
        changed = []
        if self.post is not None:
            changed = sorted(path for path in set(self.baseline) | set(self.post) if self.baseline.get(path) != self.post.get(path))
        payload = {
            "schemaVersion": 1,
            "attemptId": self.id,
            "changedFiles": changed,
            "workUnits": [
                {
                    "id": str(item.get("id") or item.get("workUnitId") or ""),
                    "kind": str(item.get("kind") or item.get("workUnitKind") or ""),
                    "status": str(item.get("status") or ""),
                    "changedFiles": sorted(set(item.get("changedFiles") or item.get("changed_files") or [])),
                }
                for item in (work_units or [])
            ],
            "artifacts": [
                {
                    "id": str(item.get("id") or item.get("artifactId") or ""),
                    "kind": str(item.get("kind") or item.get("type") or ""),
                    "workUnitId": str(item.get("workUnitId") or item.get("work_unit_id") or ""),
                    "sha256": str(item.get("sha256") or item.get("digest") or ""),
                }
                for item in (artifacts or [])
            ],
        }
        self.store.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.manifest_path

    def finalize(self) -> "AttemptSnapshot":
        status_paths = {
            line[3:].strip().split(" -> ")[-1]
            for line in _git(self.root, "status", "--short").splitlines()
            if len(line) > 3
        }
        paths = set(self.baseline) | status_paths
        post = {path: _hash(self.root / path) for path in paths if path}
        paths = set(self.baseline) | status_paths
        post_index = _index_entries(self.root, paths)
        payload = {
            "id": self.id,
            "baseline": self.baseline,
            "baselineStatus": sorted(self.baseline_status),
            "baselineIndex": self.baseline_index,
            "post": post,
            "postIndex": post_index,
        }
        self.store.mkdir(parents=True, exist_ok=True)
        self.metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return AttemptSnapshot(self.id, self.root, self.store, self.baseline, self.baseline_status, self.baseline_index, post, post_index)

    def preview_restore(self) -> tuple[bool, list[str]]:
        """Check whether restore can proceed without modifying the workspace."""
        if self.post is None:
            return False, ["snapshot is not finalized"]
        conflicts: list[str] = []
        paths = set(self.baseline) | set(self.post)
        for path in sorted(paths):
            before = self.baseline.get(path)
            current = _hash(self.root / path)
            after = self.post.get(path)
            if current == before:
                continue
            if current != after:
                conflicts.append(path)
        post_index = self.post_index or {}
        current_index = _index_entries(self.root, set(self.baseline_index) | set(post_index))
        for path in sorted(set(self.baseline_index) | set(post_index)):
            if current_index.get(path) != self.baseline_index.get(path) and current_index.get(path) != post_index.get(path):
                conflicts.append(f"index:{path}")
        if conflicts:
            return False, conflicts
        return True, []

    def restore(self) -> tuple[bool, list[str]]:
        """Restore only when the complete workspace/index preflight is clean."""
        ok, conflicts = self.preview_restore()
        if not ok:
            return False, conflicts
        paths = set(self.baseline) | set(self.post or {})
        post_index = self.post_index or {}

        for path in sorted(paths):
            before = self.baseline.get(path)
            current = _hash(self.root / path)
            after = self.post.get(path)
            if current == before:
                continue
            target = self.store / "files" / path
            if before is None:
                try:
                    (self.root / path).unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    return False, [path]
            elif target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, self.root / path)
            else:
                restore_args = ["git", "-C", str(self.root), "restore"]
                if path not in self.baseline_status:
                    restore_args.extend(["--source", "HEAD"])
                restore_args.extend(["--worktree", "--", path])
                proc = subprocess.run(restore_args, capture_output=True, timeout=10)
                if proc.returncode != 0:
                    return False, [path]

        for path, entry in self.baseline_index.items():
            meta, _ = entry.split("\t", 1)
            mode, object_id, stage = meta.split()
            proc = subprocess.run(
                ["git", "-C", str(self.root), "update-index", "--add", "--cacheinfo", f"{mode},{object_id},{path}"],
                capture_output=True,
                timeout=10,
            )
            if proc.returncode != 0:
                return False, [f"index:{path}"]
        for path in set(post_index) - set(self.baseline_index):
            proc = subprocess.run(["git", "-C", str(self.root), "update-index", "--force-remove", "--", path], capture_output=True, timeout=10)
            if proc.returncode != 0 and _index_entries(self.root, {path}).get(path):
                return False, [f"index:{path}"]
        return True, []


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
    baseline_index = _index_entries(root, paths)
    snapshot = AttemptSnapshot(snapshot_id, root, store_root / snapshot_id, baseline, frozenset(status_paths), baseline_index)
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
        return AttemptSnapshot(
            snapshot_id,
            root.resolve(),
            store / snapshot_id,
            dict(payload["baseline"]),
            frozenset(payload.get("baselineStatus", [])),
            dict(payload.get("baselineIndex", {})),
            dict(payload.get("post", {})),
            dict(payload.get("postIndex", {})),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


__all__ = ["AttemptSnapshot", "capture_attempt", "load_snapshot"]
