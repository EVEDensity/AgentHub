"""SQLite DDL translation for the Mission control-plane schema (local profile).

Split out of ``app.db.init_db``. The Postgres migration chain mixes plain DDL
with PL/pgSQL DO blocks and ``ALTER ... ADD CONSTRAINT`` statements; SQLite
cannot run the procedural statements and does not enforce the added named
constraints, so those are skipped and everything else is translated to
SQLite-compatible types.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("agenthub.db.init")

_MISSION_CONTROL_PLANE_SQLITE_UPGRADES = (
    "MISSION_CONTROL_PLANE_UPGRADE",
    "MISSION_EVENT_LEDGER_UPGRADE",
    "WORK_UNIT_PERSISTENCE_UPGRADE",
    "A2A_SOURCE_MAPPING_UPGRADE",
    "MISSION_ARTIFACT_TABLE_UPGRADE",
    "ARTIFACT_PERSISTENCE_UPGRADE",
    "EVIDENCE_PROJECTION_UPGRADE",
    "DELEGATION_PERSISTENCE_UPGRADE",
    "AGENT_BINDING_PERSISTENCE_UPGRADE",
    "AGENT_CATALOG_PROJECTION_UPGRADE",
    "A2A_INBOUND_SOURCE_MAPPING_UPGRADE",
    "DECISION_PERSISTENCE_UPGRADE",
    "DECISION_EXPIRY_UPGRADE",
    "ARTIFACT_TABLE_OWNERSHIP_UPGRADE",
    "CONTRACT_REVISION_BINDING_UPGRADE",
    "CONTRACT_LINEAGE_OWNERSHIP_UPGRADE",
    "EXECUTION_CHECKPOINT_UPGRADE",
)

# Public aliases for the moved symbols (the historical names above stay the
# canonical ones so ``app.db.init_db`` re-exports keep working).
MISSION_CONTROL_PLANE_SQLITE_UPGRADES = _MISSION_CONTROL_PLANE_SQLITE_UPGRADES


def _strip_check_constraints(text: str) -> str:
    """Remove every ``CHECK (…)`` group with balanced-paren matching.

    The Postgres CHECK clauses use server-only functions (jsonb_typeof) and
    operators (~) that SQLite can neither run nor parse; the local profile
    relies on application-level validation instead.
    """
    out: list[str] = []
    i = 0
    upper = text.upper()
    while True:
        pos = upper.find("CHECK", i)
        if pos == -1:
            out.append(text[i:])
            return "".join(out)
        segment = re.sub(r"CONSTRAINT\s+\"?\w+\"?\s*$", "", text[i:pos])
        out.append(segment)
        j = pos + len("CHECK")
        while j < len(text) and text[j].isspace():
            j += 1
        if j >= len(text) or text[j] != "(":
            out.append(text[pos:j])
            i = j
            continue
        depth = 0
        k = j
        while k < len(text):
            if text[k] == "(":
                depth += 1
            elif text[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        i = k + 1 if depth == 0 else len(text)


strip_check_constraints = _strip_check_constraints


async def _create_mission_control_plane_sqlite(conn) -> None:
    """Create the Mission control-plane schema on SQLite.

    The Postgres migration chain mixes plain DDL with PL/pgSQL DO blocks and
    ALTER ... ADD CONSTRAINT statements. SQLite cannot run the procedural
    statements and does not enforce the added named constraints, so those are
    skipped; everything else is translated to SQLite-compatible types. The
    local profile runs on a single serialized connection, which preserves the
    write ordering the skipped constraints would have guaranteed.
    """
    from app.db.migrations import mission_control_plane as control_plane

    for upgrade_name in _MISSION_CONTROL_PLANE_SQLITE_UPGRADES:
        statements = getattr(control_plane, upgrade_name, ())
        for statement in statements:
            text = statement.strip()
            upper_text = text.upper()
            if upper_text.startswith("DO $$"):
                continue
            if upper_text.startswith("ALTER TABLE"):
                # SQLite supports additive ALTERs only: ADD COLUMN becomes a
                # plain ADD COLUMN (its duplicate-column error is swallowed by
                # the try/except below, which is the idempotency guard);
                # constraint and column-drop ALTERs are skipped.
                if "ADD CONSTRAINT" in upper_text or "DROP CONSTRAINT" in upper_text:
                    continue
                if "ADD COLUMN" not in upper_text:
                    continue
                text = text.replace("IF NOT EXISTS ", "")
            translated = _strip_check_constraints(text)
            translated = re.sub(r",(\s*,+)+", ",", translated)
            translated = (
                translated.replace("JSONB", "TEXT")
                .replace("TIMESTAMPTZ", "TEXT")
                .replace("DOUBLE PRECISION", "REAL")
                .replace("SERIAL", "INTEGER")
                .replace("BIGSERIAL", "INTEGER")
                .replace("BOOLEAN", "INTEGER")
                .replace("BYTEA", "BLOB")
            )
            translated = re.sub(r",\s*\)", ")", translated)
            try:
                await conn.execute(translated)
            except Exception as exc:
                logger.warning(
                    "mission schema SQLite DDL skipped: %s — %s",
                    exc,
                    text[:80],
                )

    # The PostgreSQL chain adds UNIQUE (id, mission_id) on work_units through
    # a DO block that SQLite cannot run. execution_checkpoints carries a
    # composite foreign key REFERENCES work_units(id, mission_id); without a
    # matching unique parent index SQLite raises "foreign key mismatch" on
    # every checkpoint insert. Create the index the DO block would have made.
    try:
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_work_units_id_mission "
            "ON work_units(id, mission_id)"
        )
    except Exception as exc:
        logger.warning("mission schema SQLite unique index skipped: %s", exc)


create_mission_control_plane_sqlite = _create_mission_control_plane_sqlite
