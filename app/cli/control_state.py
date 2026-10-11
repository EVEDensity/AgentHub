"""Keep mutable CLI control state outside the model's writable workspace.

Legacy state is copied once, verified and retained. A marker binds the new
directory to its original state path and detects subsequent legacy divergence.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from app.services.recovery_lock import RecoveryExecutionBusy, RecoveryExecutionLock
from app.services.recovery_store import runner_state_directory

MAX_MIGRATION_FILES = 20_000
MAX_MIGRATION_BYTES = 1024 * 1024 * 1024
_MARKER = "migration.json"


def _regular_file(path: Path) -> None:
    if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
        raise RuntimeError(f"control state migration requires a regular file: {path}")


def _hash_file(path: Path) -> str:
    _regular_file(path)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if size > MAX_MIGRATION_BYTES:
                raise RuntimeError("control state file exceeds migration byte limit")
            digest.update(chunk)
    return digest.hexdigest()


def _data_files(data: Path) -> list[Path]:
    if not data.exists():
        return []
    files, directories = [], [data]
    count = 0
    while directories:
        directory = directories.pop()
        if directory.is_symlink() or directory.resolve() != directory.absolute():
            raise RuntimeError("legacy control data contains a link")
        # scandir errors propagate: a partial inventory must never be copied.
        with os.scandir(directory) as entries:
            for item in entries:
                count += 1
                if count > MAX_MIGRATION_FILES:
                    raise RuntimeError("legacy control state exceeds migration file limit")
                entry = Path(item.path)
                if entry.is_symlink():
                    raise RuntimeError("legacy control data contains a link")
                if entry.is_dir():
                    directories.append(entry)
                else:
                    _regular_file(entry)
                    files.append(entry)
    return files


def _source_files(legacy: Path) -> list[Path]:
    database_dir = legacy / "db"
    if database_dir.is_symlink() or database_dir.resolve() != database_dir.absolute():
        raise RuntimeError("legacy control database directory must not be a symlink")
    files = [legacy / "db" / "agenthub.db"]
    wal = legacy / "db" / "agenthub.db-wal"
    if wal.exists():
        files.append(wal)
    files.extend(_data_files(legacy / "data"))
    if len(files) > MAX_MIGRATION_FILES:
        raise RuntimeError("legacy control state exceeds migration file limit")
    if sum(path.stat().st_size for path in files) > MAX_MIGRATION_BYTES:
        raise RuntimeError("legacy control state exceeds migration byte limit")
    return sorted(files)


def _source_digest(legacy: Path) -> str:
    paths = _source_files(legacy)
    digest = hashlib.sha256(_database_digest(legacy / "db" / "agenthub.db").encode("ascii"))
    for path in paths:
        if path.relative_to(legacy).parts[0] != "data":
            continue
        digest.update(path.relative_to(legacy).as_posix().encode("utf-8") + b"\0")
        digest.update(_hash_file(path).encode("ascii") + b"\0")
    return digest.hexdigest()


def _database_digest(path: Path) -> str:
    """Hash committed logical rows, so ordinary WAL checkpointing is harmless."""
    _regular_file(path)
    digest = hashlib.sha256()
    deadline = time.monotonic() + 30
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        size = 0
        for statement in connection.iterdump():
            encoded = statement.encode("utf-8")
            size += len(encoded)
            if size > 3 * MAX_MIGRATION_BYTES or time.monotonic() > deadline:
                raise RuntimeError("control database fingerprint exceeded migration bounds")
            digest.update(encoded + b"\0")
        connection.rollback()
    return digest.hexdigest()


def _backup_database(source: Path, destination: Path) -> None:
    _regular_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as old, closing(sqlite3.connect(destination)) as new:
        deadline = time.monotonic() + 30
        page_size = old.execute("PRAGMA page_size").fetchone()[0]

        def progress(_status: int, _remaining: int, total: int) -> None:
            if time.monotonic() > deadline or total * page_size > MAX_MIGRATION_BYTES:
                raise RuntimeError("control database backup exceeded migration bounds")

        old.backup(new, pages=256, progress=progress)
        if new.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise RuntimeError("migrated control database failed integrity check")


def _copy_data(legacy: Path, temporary: Path) -> None:
    size = 0
    for source in _source_files(legacy):
        relative = source.relative_to(legacy)
        if relative.parts[0] != "data":
            continue
        destination = temporary / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as old, destination.open("xb") as new:
            for chunk in iter(lambda: old.read(1024 * 1024), b""):
                size += len(chunk)
                if size > MAX_MIGRATION_BYTES:
                    raise RuntimeError("control data exceeds migration byte limit during copy")
                new.write(chunk)
        if _hash_file(source) != _hash_file(destination):
            raise RuntimeError("migrated control data failed byte verification")


def _existing_target(target: Path, legacy: Path) -> Path:
    if target.is_symlink() or not target.is_dir():
        raise RuntimeError("control state target is not a private directory")
    marker = target / _MARKER
    try:
        _regular_file(marker)
        if marker.stat().st_size > 8192:
            raise ValueError("oversized migration marker")
        metadata = json.loads(marker.read_text(encoding="utf-8"))
        if metadata.get("version") != 1 or metadata.get("source") != str(legacy):
            raise ValueError("migration source differs")
        expected = metadata.get("legacyDigest")
        if (legacy / "db" / "agenthub.db").exists():
            if not expected or _source_digest(legacy) != expected:
                raise ValueError("legacy state changed after migration")
        elif expected:
            raise ValueError("legacy migration source is missing")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError("existing control state conflicts with legacy state; no files overwritten") from exc
    return target


def _remove_temporary(temporary: Path, base: Path) -> None:
    # This helper owns only its random migration directory, never user state.
    resolved = temporary.resolve()
    if resolved.parent != base.resolve() or not resolved.name.startswith(".cm-"):
        raise RuntimeError("unsafe migration temporary directory")
    shutil.rmtree(resolved)


def _initialize(target: Path, legacy: Path) -> Path:
    base = target.parent
    try:
        lock = RecoveryExecutionLock(base, "cli-control-state-migration")
    except RecoveryExecutionBusy as exc:
        raise RuntimeError("control state migration is already in progress") from exc
    temporary = base / (".cm-" + uuid.uuid4().hex[:12])
    try:
        if target.exists():
            return _existing_target(target, legacy)
        temporary.mkdir(mode=0o700)
        database = legacy / "db" / "agenthub.db"
        source_digest = None
        if database.exists():
            source_digest = _source_digest(legacy)
            _backup_database(database, temporary / "db" / "agenthub.db")
            _copy_data(legacy, temporary)
            if _source_digest(legacy) != source_digest:
                raise RuntimeError("legacy state changed during migration")
        metadata = {"version": 1, "source": str(legacy), "legacyDigest": source_digest}
        (temporary / _MARKER).write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        temporary.rename(target)
        return target
    finally:
        try:
            if temporary.exists():
                _remove_temporary(temporary, base)
        finally:
            lock.close()


def control_state_directory(
    workspace: Path, legacy: Path, *, initialize: bool = False,
    state_root: Path | None = None,
) -> Path:
    """Resolve mutable runtime storage; initialize performs verified migration."""
    legacy = Path(legacy)
    if legacy.is_symlink():
        raise RuntimeError("legacy control state must not be a symlink")
    legacy = legacy.resolve()
    target = runner_state_directory(workspace, state_root) / "control-state"
    if target.exists():
        return _existing_target(target, legacy)
    return _initialize(target, legacy) if initialize else target


def has_control_database(workspace: Path, legacy: Path) -> bool:
    target = control_state_directory(workspace, legacy)
    return (target / "db" / "agenthub.db").is_file() or (legacy / "db" / "agenthub.db").is_file()


def control_database_path(workspace: Path, legacy: Path) -> Path:
    """Migrate existing history before any direct read of session/event rows."""
    if has_control_database(workspace, legacy):
        return control_state_directory(workspace, legacy, initialize=True) / "db" / "agenthub.db"
    return control_state_directory(workspace, legacy) / "db" / "agenthub.db"
