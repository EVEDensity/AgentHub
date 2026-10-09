"""Read-only storage readiness for both private and retained CLI control data."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.cli.control_state import control_state_directory


def doctor_sqlite(cwd: Path, state: Path) -> dict[str, Any]:
    """Check the local SQLite store without modifying business tables."""
    import sqlite3

    configured = os.environ.get("AGENTHUB_SQLITE_PATH", "").strip()
    configured_path = None
    if configured:
        configured_path = Path(configured)
        if not configured_path.is_absolute():
            configured_path = cwd / configured_path
    try:
        private_database = control_state_directory(cwd, state) / "db" / "agenthub.db"
    except (RuntimeError, OSError):
        return {"ok": False, "path": "<private-control-state>", "integrity": "control_state_conflict"}
    candidates = [
        configured_path,
        private_database,
        state / "db" / "agenthub.db",
        cwd / ".agenthub" / "db" / "agenthub.db",
        cwd / ".agenthub" / "agenthub.db",
    ]
    database = next((path for path in candidates if path and path.is_file()), None)
    if database is None:
        return {"ok": True, "skipped": True, "reason": "database_not_created"}

    relative = str(database)
    try:
        relative = str(database.resolve().relative_to(cwd.resolve()))
        relative = "<workspace>/" + relative.replace("\\", "/")
    except (OSError, ValueError):
        relative = "<configured-sqlite>"
    connection = None
    try:
        connection = sqlite3.connect(database, timeout=3)
        quick = connection.execute("PRAGMA quick_check").fetchone()
        integrity = str(quick[0]) if quick else "unknown"
        connection.execute("CREATE TEMP TABLE __agenthub_doctor_probe (value INTEGER)")
        connection.execute("INSERT INTO __agenthub_doctor_probe(value) VALUES (1)")
        readback = connection.execute(
            "SELECT value FROM __agenthub_doctor_probe"
        ).fetchone()
        connection.rollback()
        healthy = integrity.lower() == "ok" and readback == (1,)
        return {
            "ok": healthy,
            "path": relative,
            "integrity": integrity,
            "readWrite": readback == (1,),
        }
    except (OSError, sqlite3.DatabaseError) as exc:
        return {
            "ok": False,
            "path": relative,
            "integrity": "error",
            "readWrite": False,
            "errorType": type(exc).__name__,
        }
    finally:
        if connection is not None:
            connection.close()
