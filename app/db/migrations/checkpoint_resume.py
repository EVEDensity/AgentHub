"""Additive checkpoint resume metadata; keep the original revision immutable."""

from __future__ import annotations

from typing import Any

from app.db.migrations.mission_control_plane import EXECUTION_CHECKPOINT_REVISION

EXECUTION_CHECKPOINT_RESUME_REVISION = "b7e1f203c4d5"
EXECUTION_CHECKPOINT_RESUME_DOWN_REVISION = EXECUTION_CHECKPOINT_REVISION

_RESUME_COLUMNS = (
    ("resume_protocol_version", "INTEGER"),
    ("next_action", "JSONB"),
    ("idempotency_key", "TEXT"),
    ("workspace_revision", "TEXT"),
    ("context_manifest_digest", "TEXT"),
)

EXECUTION_CHECKPOINT_RESUME_UPGRADE = tuple(
    f"ALTER TABLE execution_checkpoints ADD COLUMN IF NOT EXISTS {name} {kind}"
    for name, kind in _RESUME_COLUMNS
)
EXECUTION_CHECKPOINT_RESUME_DOWNGRADE = tuple(
    f"ALTER TABLE execution_checkpoints DROP COLUMN IF EXISTS {name}"
    for name, _kind in reversed(_RESUME_COLUMNS)
)


async def upgrade_checkpoint_resume_sqlite(connection: Any) -> None:
    """Add missing fields atomically without inventing legacy fingerprints.

    SQLite has no ADD COLUMN IF NOT EXISTS, so inspect the table within the
    migration transaction. A missing table or failed ALTER must fail startup.
    The caller also includes its schema marker in the outer transaction.
    """
    async with connection.transaction():
        rows = await connection.fetch("PRAGMA table_info(execution_checkpoints)")
        if not rows:
            raise RuntimeError(
                "execution_checkpoints table is missing; cannot upgrade resume metadata"
            )
        existing = {row["name"] for row in rows}
        for name, kind in _RESUME_COLUMNS:
            if name not in existing:
                sqlite_kind = "TEXT" if kind == "JSONB" else kind
                await connection.execute(
                    f"ALTER TABLE execution_checkpoints ADD COLUMN {name} {sqlite_kind}"
                )
