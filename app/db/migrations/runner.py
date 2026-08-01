from __future__ import annotations

import logging
from typing import Any

from app.db.migrations.mission_control_plane import (
    MISSION_CONTROL_PLANE_DOWN_REVISION,
    MISSION_CONTROL_PLANE_REVISION,
    MISSION_CONTROL_PLANE_UPGRADE,
)


class UnsupportedMigrationPath(RuntimeError):
    """Raised when startup cannot safely advance the recorded database revision."""


async def apply_startup_migrations(
    connection: Any,
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Apply runtime-supported migrations before the legacy fallback DDL."""

    migration_logger = logger or logging.getLogger("agenthub.db.migrations")
    await connection.execute(
        """CREATE TABLE IF NOT EXISTS alembic_version (
            version_num TEXT PRIMARY KEY
        )"""
    )
    row = await connection.fetchrow("SELECT version_num FROM alembic_version LIMIT 1")
    current = row["version_num"] if row else None
    if current == MISSION_CONTROL_PLANE_REVISION:
        migration_logger.info("init_db: Alembic already at head (%s)", current)
        return

    if current not in {None, MISSION_CONTROL_PLANE_DOWN_REVISION}:
        message = (
            "unsupported Alembic upgrade path "
            f"(current={current}, head={MISSION_CONTROL_PLANE_REVISION}); "
            "run 'alembic upgrade head' offline before starting AgentHub"
        )
        migration_logger.error("init_db: %s", message)
        raise UnsupportedMigrationPath(message)

    for statement in MISSION_CONTROL_PLANE_UPGRADE:
        await connection.execute(statement)

    if current is None:
        await connection.execute(
            "INSERT INTO alembic_version(version_num) VALUES($1)",
            MISSION_CONTROL_PLANE_REVISION,
        )
    else:
        await connection.execute(
            "UPDATE alembic_version SET version_num=$1 WHERE version_num=$2",
            MISSION_CONTROL_PLANE_REVISION,
            current,
        )
    migration_logger.info(
        "init_db: Alembic advanced from %s to %s",
        current or "unversioned",
        MISSION_CONTROL_PLANE_REVISION,
    )
