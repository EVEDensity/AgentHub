"""Add explicit v1 session ownership without guessing legacy workspace scope."""
from __future__ import annotations

from typing import Any

from app.db.migrations.checkpoint_resume import EXECUTION_CHECKPOINT_RESUME_REVISION
from app.db.migrations.mission_control_plane import (
    PENDING_CONFIRMATIONS_UPGRADE,
    SESSION_EVENTS_UPGRADE,
)

SESSION_WORKSPACE_REVISION = "c8f2a314d5e6"
SESSION_WORKSPACE_DOWN_REVISION = EXECUTION_CHECKPOINT_RESUME_REVISION

# Both the historical legacy table and the previously proposed v1-only table
# are supported. Explicit workspace scope is nullable for historical rows;
# no owner_id/participant backfill is an authorization source.
_SESSION_COLUMNS = (
    ("name", "TEXT NOT NULL DEFAULT ''"),
    ("type", "TEXT NOT NULL DEFAULT 'group'"),
    ("participants", "TEXT NOT NULL DEFAULT '[]'"),
    ("active", "INTEGER NOT NULL DEFAULT 1"),
    ("is_pinned", "INTEGER NOT NULL DEFAULT 0"),
    ("last_message_at", "TEXT NOT NULL DEFAULT ''"),
    ("owner_id", "TEXT NOT NULL DEFAULT ''"),
    ("visibility", "TEXT NOT NULL DEFAULT 'private'"),
    ("workspace_id", "TEXT"),
    ("title", "TEXT"),
    ("status", "TEXT"),
    ("metadata", "JSONB"),
    ("created_by_type", "TEXT"),
    ("created_by_id", "TEXT"),
    ("created_by_display_name", "TEXT NOT NULL DEFAULT ''"),
    ("updated_at", "TEXT"),
)

_CREATE_SESSION_TABLE = """CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL
)"""
_SESSION_INDEX = """CREATE INDEX IF NOT EXISTS idx_sessions_workspace
    ON sessions(workspace_id, created_at DESC)"""

SESSION_WORKSPACE_UPGRADE = (
    _CREATE_SESSION_TABLE,
    *(
        f"ALTER TABLE sessions ADD COLUMN IF NOT EXISTS {name} {kind}"
        for name, kind in _SESSION_COLUMNS
    ),
    # Legacy transports store ISO text. Retain that compatible representation
    # even when the experimental v1-only schema used native timestamps.
    "ALTER TABLE sessions ALTER COLUMN created_at TYPE TEXT USING created_at::TEXT",
    "ALTER TABLE sessions ALTER COLUMN updated_at TYPE TEXT USING updated_at::TEXT",
    _SESSION_INDEX,
    *SESSION_EVENTS_UPGRADE,
    *PENDING_CONFIRMATIONS_UPGRADE,
)

# Application rollback must retain scoped conversations and their receipts.
# The additive compatibility schema is readable by the prior application.
SESSION_WORKSPACE_DOWNGRADE: tuple[str, ...] = ()


async def upgrade_session_workspace_sqlite(connection: Any) -> None:
    """Upgrade real local profiles atomically; legacy ownership stays unknown."""
    from app.db.sqlite_translator import strip_check_constraints

    async with connection.transaction():
        rows = await connection.fetch("PRAGMA table_info(sessions)")
        if not rows:
            raise RuntimeError("sessions table is missing; cannot establish session scope")
        existing = {row["name"] for row in rows}
        if not {"id", "created_at"} <= existing:
            raise RuntimeError("sessions table is incompatible with the v1 session schema")
        for name, kind in _SESSION_COLUMNS:
            if name not in existing:
                await connection.execute(
                    f"ALTER TABLE sessions ADD COLUMN {name} {kind.replace('JSONB', 'TEXT')}"
                )
        await connection.execute(_SESSION_INDEX)
        for statement in (*SESSION_EVENTS_UPGRADE, *PENDING_CONFIRMATIONS_UPGRADE):
            sqlite_statement = strip_check_constraints(statement)
            sqlite_statement = sqlite_statement.replace("JSONB", "TEXT").replace("TIMESTAMPTZ", "TEXT")
            await connection.execute(sqlite_statement)
