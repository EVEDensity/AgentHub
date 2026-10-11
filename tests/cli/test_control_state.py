"""Verified migration keeps control writes outside the model workspace."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.cli import control_state, runtime
from app.cli.doctor_storage import doctor_sqlite
from app.services.recovery_lock import RecoveryExecutionLock
from app.services.workspace_fingerprint import workspace_revision


def _database(path: Path, value: str = "mission-original") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE missions (id TEXT)")
        connection.execute("INSERT INTO missions VALUES (?)", (value,))


def _value(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT id FROM missions").fetchone()[0]


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Keep the private root short enough for Windows' default path limit.
    return workspace, workspace / ".agenthub", tmp_path.parent / ("cs-" + uuid.uuid4().hex[:8])


def test_verified_legacy_migration_preserves_database_artifacts_and_original_source(tmp_path):
    workspace, legacy, private = _paths(tmp_path)
    database = legacy / "db" / "agenthub.db"
    _database(database)
    artifact = legacy / "data" / "data" / "artifacts" / "ab" / ("a" * 64)
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"actual registered Artifact bytes")
    (legacy / "config.json").write_text('{"model":"mock"}')
    original = database.read_bytes()
    target = control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private)
    assert workspace not in target.parents
    assert _value(target / "db" / "agenthub.db") == "mission-original"
    assert (target / artifact.relative_to(legacy)).read_bytes() == artifact.read_bytes()
    assert database.read_bytes() == original
    assert not (target / "config.json").exists()
    assert control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private) == target
    # Advancing the private database must never copy the retained old snapshot back.
    with sqlite3.connect(target / "db" / "agenthub.db") as connection:
        connection.execute("UPDATE missions SET id='mission-new'")
    control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private)
    assert _value(target / "db" / "agenthub.db") == "mission-new"
    assert _value(database) == "mission-original"


def test_backup_preserves_committed_wal_rows(tmp_path):
    workspace, legacy, private = _paths(tmp_path)
    database = legacy / "db" / "agenthub.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as source:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.execute("CREATE TABLE missions (id TEXT)")
        source.execute("INSERT INTO missions VALUES ('mission-wal')")
        source.commit()
        target = control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private)
        assert _value(target / "db" / "agenthub.db") == "mission-wal"
    # Closing the old writer checkpoints WAL pages into .db without changing
    # any business rows. A logical marker must permit the next CLI boot.
    assert control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private) == target


def test_changed_legacy_state_never_overwrites_private_state(tmp_path):
    workspace, legacy, private = _paths(tmp_path)
    database = legacy / "db" / "agenthub.db"
    _database(database)
    target = control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE missions SET id='different-original'")
    before = (target / "db" / "agenthub.db").read_bytes()
    with pytest.raises(RuntimeError, match="conflicts"):
        control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private)
    assert (target / "db" / "agenthub.db").read_bytes() == before


def test_unmarked_existing_target_is_not_overwritten(tmp_path):
    workspace, legacy, private = _paths(tmp_path)
    target = control_state.control_state_directory(workspace, legacy, state_root=private)
    _database(target / "db" / "agenthub.db", "target-original")
    _database(legacy / "db" / "agenthub.db")
    with pytest.raises(RuntimeError):
        control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private)
    assert _value(target / "db" / "agenthub.db") == "target-original"


@pytest.mark.parametrize("failure", ["database", "data"])
def test_failed_copy_is_atomic_and_retains_legacy_source(tmp_path, failure):
    workspace, legacy, private = _paths(tmp_path)
    _database(legacy / "db" / "agenthub.db")
    target = control_state.control_state_directory(workspace, legacy, state_root=private)
    helper = "_backup_database" if failure == "database" else "_copy_data"
    with patch.object(control_state, helper, side_effect=RuntimeError("injected copy failure")), pytest.raises(RuntimeError, match="injected"):
        control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private)
    assert not target.exists()
    assert not list(target.parent.glob(".cm-*"))
    assert not (target.parent / ".control-migration.lock").exists()
    assert _value(legacy / "db" / "agenthub.db") == "mission-original"
    control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private)
    assert _value(target / "db" / "agenthub.db") == "mission-original"


@pytest.mark.parametrize("limit", ["MAX_MIGRATION_FILES", "MAX_MIGRATION_BYTES"])
def test_oversized_legacy_copy_is_refused(tmp_path, limit):
    workspace, legacy, private = _paths(tmp_path)
    _database(legacy / "db" / "agenthub.db")
    target = control_state.control_state_directory(workspace, legacy, state_root=private)
    with patch.object(control_state, limit, 0), pytest.raises(RuntimeError, match="limit"):
        control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private)
    assert not target.exists()


def test_corrupt_database_is_not_published(tmp_path):
    workspace, legacy, private = _paths(tmp_path)
    database = legacy / "db" / "agenthub.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not a SQLite database")
    target = control_state.control_state_directory(workspace, legacy, state_root=private)
    with pytest.raises(sqlite3.DatabaseError):
        control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private)
    assert not target.exists()
    assert database.read_bytes() == b"not a SQLite database"


def test_process_death_during_migration_releases_lock_and_next_boot_retries(tmp_path):
    workspace, legacy, private = _paths(tmp_path)
    _database(legacy / "db" / "agenthub.db")
    script = """
import os, sys
from pathlib import Path
from app.cli import control_state
control_state._backup_database = lambda *_: os._exit(23)
control_state.control_state_directory(Path(sys.argv[1]), Path(sys.argv[2]),
                                     initialize=True, state_root=Path(sys.argv[3]))
"""
    result = subprocess.run([sys.executable, "-c", script, str(workspace), str(legacy), str(private)],
                            cwd=Path(__file__).resolve().parents[2], timeout=10, check=False)
    assert result.returncode == 23
    target = control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private)
    assert _value(target / "db" / "agenthub.db") == "mission-original"
    assert _value(legacy / "db" / "agenthub.db") == "mission-original"


def test_live_migration_lock_refuses_a_second_initializer(tmp_path):
    workspace, legacy, private = _paths(tmp_path)
    target = control_state.control_state_directory(workspace, legacy, state_root=private)
    lock = RecoveryExecutionLock(target.parent, "cli-control-state-migration")
    try:
        with pytest.raises(RuntimeError, match="already in progress"):
            control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private)
        assert not target.exists()
    finally:
        lock.close()
    assert control_state.control_state_directory(workspace, legacy, initialize=True, state_root=private) == target


def test_process_env_uses_only_private_control_runtime_paths(tmp_path, monkeypatch):
    workspace, legacy, private = _paths(tmp_path)
    monkeypatch.setenv("AGENTHUB_RUNNER_STATE_ROOT", str(private))
    process = runtime.MissionControlProcess(
        state_dir=legacy, workspace_root=workspace,
        model=runtime.CliModelSettings("mock", "mock", "", ""),
        project_instructions="actual project instructions",
    )
    child = Mock()
    child.poll.return_value = 0
    with patch.object(runtime.subprocess, "Popen", return_value=child) as spawn:
        with patch.object(process, "_wait_ready"):
            process.start()
        process.stop()
    env = spawn.call_args.kwargs["env"]
    for key in ("AGENTHUB_SQLITE_PATH", "AGENTHUB_LOCAL_DATA", "AGENTHUB_DESKTOP_PROJECT_INSTRUCTIONS_FILE"):
        assert workspace not in Path(env[key]).parents
    assert not (legacy / "db").exists()
    assert not (legacy / "logs").exists()


def test_history_and_doctor_find_private_database(tmp_path, monkeypatch):
    workspace, legacy, private = _paths(tmp_path)
    monkeypatch.setenv("AGENTHUB_RUNNER_STATE_ROOT", str(private))
    target = control_state.control_state_directory(workspace, legacy, initialize=True)
    _database(target / "db" / "agenthub.db")
    assert control_state.has_control_database(workspace, legacy)
    result = doctor_sqlite(workspace, legacy)
    assert result["ok"]
    assert result["readWrite"]
    assert not (legacy / "db").exists()
    assert json.loads((target / "migration.json").read_text())["legacyDigest"] is None


def test_stop_preserves_unfinished_workspace_scratch_and_checkpoint_fingerprint(tmp_path):
    workspace, legacy, _private = _paths(tmp_path)
    (workspace / ".agenthub_exec").mkdir()
    (legacy / "change-transactions").mkdir(parents=True)
    journal = legacy / "change-transactions" / "attempt.json"
    journal.write_text('{"status":"unfinished"}')
    revision = workspace_revision(workspace)
    process = runtime.MissionControlProcess(state_dir=legacy, workspace_root=workspace,
                                            model=runtime.CliModelSettings("mock", "mock", "", ""))
    process.stop()
    assert journal.read_text() == '{"status":"unfinished"}'
    assert (workspace / ".agenthub_exec").is_dir()
    assert workspace_revision(workspace) == revision
