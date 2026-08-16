from __future__ import annotations

import logging
from typing import Any

from app.db.migrations.mission_control_plane import (
    A2A_INBOUND_SOURCE_MAPPING_DOWN_REVISION,
    A2A_INBOUND_SOURCE_MAPPING_UPGRADE,
    A2A_SOURCE_MAPPING_DOWN_REVISION,
    A2A_SOURCE_MAPPING_UPGRADE,
    AGENT_BINDING_PERSISTENCE_DOWN_REVISION,
    AGENT_BINDING_PERSISTENCE_UPGRADE,
    AGENT_CATALOG_PROJECTION_DOWN_REVISION,
    AGENT_CATALOG_PROJECTION_UPGRADE,
    ARTIFACT_PERSISTENCE_DOWN_REVISION,
    ARTIFACT_PERSISTENCE_UPGRADE,
    ARTIFACT_TABLE_OWNERSHIP_DOWN_REVISION,
    ARTIFACT_TABLE_OWNERSHIP_UPGRADE,
    CONTRACT_REVISION_BINDING_DOWN_REVISION,
    CONTRACT_REVISION_BINDING_REVISION,
    CONTRACT_REVISION_BINDING_UPGRADE,
    DECISION_EXPIRY_DOWN_REVISION,
    DECISION_EXPIRY_UPGRADE,
    DECISION_PERSISTENCE_DOWN_REVISION,
    DECISION_PERSISTENCE_UPGRADE,
    DELEGATION_PERSISTENCE_DOWN_REVISION,
    DELEGATION_PERSISTENCE_UPGRADE,
    EVIDENCE_PROJECTION_DOWN_REVISION,
    EVIDENCE_PROJECTION_UPGRADE,
    MISSION_CONTROL_PLANE_DOWN_REVISION,
    MISSION_CONTROL_PLANE_UPGRADE,
    MISSION_EVENT_LEDGER_DOWN_REVISION,
    MISSION_EVENT_LEDGER_UPGRADE,
    WORK_UNIT_PERSISTENCE_DOWN_REVISION,
    WORK_UNIT_PERSISTENCE_UPGRADE,
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
    if current == CONTRACT_REVISION_BINDING_REVISION:
        migration_logger.info("init_db: Alembic already at head (%s)", current)
        return

    if current not in {
        None,
        MISSION_CONTROL_PLANE_DOWN_REVISION,
        MISSION_EVENT_LEDGER_DOWN_REVISION,
        WORK_UNIT_PERSISTENCE_DOWN_REVISION,
        A2A_SOURCE_MAPPING_DOWN_REVISION,
        ARTIFACT_PERSISTENCE_DOWN_REVISION,
        EVIDENCE_PROJECTION_DOWN_REVISION,
        DELEGATION_PERSISTENCE_DOWN_REVISION,
        AGENT_BINDING_PERSISTENCE_DOWN_REVISION,
        AGENT_CATALOG_PROJECTION_DOWN_REVISION,
        A2A_INBOUND_SOURCE_MAPPING_DOWN_REVISION,
        DECISION_PERSISTENCE_DOWN_REVISION,
        DECISION_EXPIRY_DOWN_REVISION,
        ARTIFACT_TABLE_OWNERSHIP_DOWN_REVISION,
        CONTRACT_REVISION_BINDING_DOWN_REVISION,
    }:
        message = (
            "unsupported Alembic upgrade path "
            f"(current={current}, head={CONTRACT_REVISION_BINDING_REVISION}); "
            "run 'alembic upgrade head' offline before starting AgentHub"
        )
        migration_logger.error("init_db: %s", message)
        raise UnsupportedMigrationPath(message)

    if current in {None, MISSION_CONTROL_PLANE_DOWN_REVISION}:
        for statement in MISSION_CONTROL_PLANE_UPGRADE:
            await connection.execute(statement)

    if current in {
        None,
        MISSION_CONTROL_PLANE_DOWN_REVISION,
        MISSION_EVENT_LEDGER_DOWN_REVISION,
    }:
        for statement in MISSION_EVENT_LEDGER_UPGRADE:
            await connection.execute(statement)

    if current in {
        None,
        MISSION_CONTROL_PLANE_DOWN_REVISION,
        MISSION_EVENT_LEDGER_DOWN_REVISION,
        WORK_UNIT_PERSISTENCE_DOWN_REVISION,
    }:
        for statement in WORK_UNIT_PERSISTENCE_UPGRADE:
            await connection.execute(statement)

    if current in {
        None,
        MISSION_CONTROL_PLANE_DOWN_REVISION,
        MISSION_EVENT_LEDGER_DOWN_REVISION,
        WORK_UNIT_PERSISTENCE_DOWN_REVISION,
        A2A_SOURCE_MAPPING_DOWN_REVISION,
    }:
        for statement in A2A_SOURCE_MAPPING_UPGRADE:
            await connection.execute(statement)

    if current in {
        None,
        MISSION_CONTROL_PLANE_DOWN_REVISION,
        MISSION_EVENT_LEDGER_DOWN_REVISION,
        WORK_UNIT_PERSISTENCE_DOWN_REVISION,
        A2A_SOURCE_MAPPING_DOWN_REVISION,
        ARTIFACT_PERSISTENCE_DOWN_REVISION,
    }:
        for statement in ARTIFACT_PERSISTENCE_UPGRADE:
            await connection.execute(statement)

    if current in {
        None,
        MISSION_CONTROL_PLANE_DOWN_REVISION,
        MISSION_EVENT_LEDGER_DOWN_REVISION,
        WORK_UNIT_PERSISTENCE_DOWN_REVISION,
        A2A_SOURCE_MAPPING_DOWN_REVISION,
        ARTIFACT_PERSISTENCE_DOWN_REVISION,
        EVIDENCE_PROJECTION_DOWN_REVISION,
    }:
        for statement in EVIDENCE_PROJECTION_UPGRADE:
            await connection.execute(statement)

    if current in {
        None,
        MISSION_CONTROL_PLANE_DOWN_REVISION,
        MISSION_EVENT_LEDGER_DOWN_REVISION,
        WORK_UNIT_PERSISTENCE_DOWN_REVISION,
        A2A_SOURCE_MAPPING_DOWN_REVISION,
        ARTIFACT_PERSISTENCE_DOWN_REVISION,
        EVIDENCE_PROJECTION_DOWN_REVISION,
        DELEGATION_PERSISTENCE_DOWN_REVISION,
    }:
        for statement in DELEGATION_PERSISTENCE_UPGRADE:
            await connection.execute(statement)

    if current in {
        None,
        MISSION_CONTROL_PLANE_DOWN_REVISION,
        MISSION_EVENT_LEDGER_DOWN_REVISION,
        WORK_UNIT_PERSISTENCE_DOWN_REVISION,
        A2A_SOURCE_MAPPING_DOWN_REVISION,
        ARTIFACT_PERSISTENCE_DOWN_REVISION,
        EVIDENCE_PROJECTION_DOWN_REVISION,
        DELEGATION_PERSISTENCE_DOWN_REVISION,
        AGENT_BINDING_PERSISTENCE_DOWN_REVISION,
    }:
        for statement in AGENT_BINDING_PERSISTENCE_UPGRADE:
            await connection.execute(statement)

    if current in {
        None,
        MISSION_CONTROL_PLANE_DOWN_REVISION,
        MISSION_EVENT_LEDGER_DOWN_REVISION,
        WORK_UNIT_PERSISTENCE_DOWN_REVISION,
        A2A_SOURCE_MAPPING_DOWN_REVISION,
        ARTIFACT_PERSISTENCE_DOWN_REVISION,
        EVIDENCE_PROJECTION_DOWN_REVISION,
        DELEGATION_PERSISTENCE_DOWN_REVISION,
        AGENT_BINDING_PERSISTENCE_DOWN_REVISION,
        AGENT_CATALOG_PROJECTION_DOWN_REVISION,
    }:
        for statement in AGENT_CATALOG_PROJECTION_UPGRADE:
            await connection.execute(statement)

    if current in {
        None,
        MISSION_CONTROL_PLANE_DOWN_REVISION,
        MISSION_EVENT_LEDGER_DOWN_REVISION,
        WORK_UNIT_PERSISTENCE_DOWN_REVISION,
        A2A_SOURCE_MAPPING_DOWN_REVISION,
        ARTIFACT_PERSISTENCE_DOWN_REVISION,
        EVIDENCE_PROJECTION_DOWN_REVISION,
        DELEGATION_PERSISTENCE_DOWN_REVISION,
        AGENT_BINDING_PERSISTENCE_DOWN_REVISION,
        AGENT_CATALOG_PROJECTION_DOWN_REVISION,
        A2A_INBOUND_SOURCE_MAPPING_DOWN_REVISION,
    }:
        for statement in A2A_INBOUND_SOURCE_MAPPING_UPGRADE:
            await connection.execute(statement)

    if current in {
        None,
        MISSION_CONTROL_PLANE_DOWN_REVISION,
        MISSION_EVENT_LEDGER_DOWN_REVISION,
        WORK_UNIT_PERSISTENCE_DOWN_REVISION,
        A2A_SOURCE_MAPPING_DOWN_REVISION,
        ARTIFACT_PERSISTENCE_DOWN_REVISION,
        EVIDENCE_PROJECTION_DOWN_REVISION,
        DELEGATION_PERSISTENCE_DOWN_REVISION,
        AGENT_BINDING_PERSISTENCE_DOWN_REVISION,
        AGENT_CATALOG_PROJECTION_DOWN_REVISION,
        A2A_INBOUND_SOURCE_MAPPING_DOWN_REVISION,
        DECISION_PERSISTENCE_DOWN_REVISION,
    }:
        for statement in DECISION_PERSISTENCE_UPGRADE:
            await connection.execute(statement)

    if current not in {
        ARTIFACT_TABLE_OWNERSHIP_DOWN_REVISION,
        CONTRACT_REVISION_BINDING_DOWN_REVISION,
    }:
        for statement in DECISION_EXPIRY_UPGRADE:
            await connection.execute(statement)

    if current != CONTRACT_REVISION_BINDING_DOWN_REVISION:
        for statement in ARTIFACT_TABLE_OWNERSHIP_UPGRADE:
            await connection.execute(statement)

    for statement in CONTRACT_REVISION_BINDING_UPGRADE:
        await connection.execute(statement)

    if current is None:
        await connection.execute(
            "INSERT INTO alembic_version(version_num) VALUES($1)",
            CONTRACT_REVISION_BINDING_REVISION,
        )
    else:
        await connection.execute(
            "UPDATE alembic_version SET version_num=$1 WHERE version_num=$2",
            CONTRACT_REVISION_BINDING_REVISION,
            current,
        )
    migration_logger.info(
        "init_db: Alembic advanced from %s to %s",
        current or "unversioned",
        CONTRACT_REVISION_BINDING_REVISION,
    )
