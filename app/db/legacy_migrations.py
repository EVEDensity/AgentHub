"""Historical PostgreSQL compatibility upgrades outside Mission state ownership."""
from __future__ import annotations

import logging

from app.config import DEFAULT_SESSION_ID, DEFAULT_USER_ID
from app.db.legacy_seeds import now

logger = logging.getLogger("agenthub.db.init")

async def _migrate_agent_registry_pg(conn) -> None:
    """Add columns that may not exist in older deployments (idempotent)."""
    migrations = [
        "ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS display_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS avatar_url TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS capability_tags TEXT NOT NULL DEFAULT '[]'",
        # ── Avatar DB storage (v3.2): BYTEA + MIME type ────────────
        #   Moves avatar binary from filesystem to PostgreSQL so DB
        #   backups naturally cover avatar data.  NULL = no avatar.
        "ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS avatar_data BYTEA",
        "ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS avatar_mime TEXT NOT NULL DEFAULT ''",
        # ── Per-user agent separation: user_id + composite PK ─────
        "ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT ''",
    ]
    for m in migrations:
        try:
            await conn.execute(m)
        except Exception as exc:
            logger.warning("agent_registry migration skipped: %s", exc)

    # ── Convert single-column PK (agent_id) → composite PK (agent_id, user_id) ──
    # This is idempotent: if the composite PK already exists the DO block is a no-op.
    try:
        await conn.execute(
            """DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'agent_registry_pkey' AND contype = 'p'
              ) THEN
                -- Only proceed if user_id is NOT yet part of the PK
                IF NOT EXISTS (
                  SELECT 1 FROM information_schema.key_column_usage
                  WHERE constraint_name = 'agent_registry_pkey' AND column_name = 'user_id'
                ) THEN
                  ALTER TABLE agent_registry DROP CONSTRAINT agent_registry_pkey;
                  ALTER TABLE agent_registry ADD PRIMARY KEY (agent_id, user_id);
                END IF;
              END IF;
            END $$;"""
        )
    except Exception as exc:
        logger.warning("agent_registry PK migration skipped: %s", exc)


async def _migrate_multi_user_pg(conn) -> None:
    """Add multi-user collaboration columns and backfill existing data (idempotent)."""
    now_ts = now()

    # 1. Add new columns to existing tables
    col_migrations = [
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS owner_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'private'",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT ''",
    ]
    for m in col_migrations:
        try:
            await conn.execute(m)
        except Exception as exc:
            logger.warning("multi_user migration skipped: %s", exc)

    # 2. Create session_members table if not exists
    try:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS session_members (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL DEFAULT 'member',
                invited_by TEXT NOT NULL DEFAULT '',
                joined_at TEXT NOT NULL,
                PRIMARY KEY (session_id, user_id)
            )"""
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sm_user ON session_members(user_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sm_session ON session_members(session_id)"
        )
    except Exception as exc:
        logger.warning("session_members migration skipped: %s", exc)

    # 3. Create user_presence table if not exists
    try:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS user_presence (
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'online',
                last_heartbeat TEXT NOT NULL,
                PRIMARY KEY (user_id, session_id)
            )"""
        )
    except Exception as exc:
        logger.warning("user_presence migration skipped: %s", exc)

    # 4. Backfill owner_id for sessions that have no owner
    orphan_sessions = await conn.fetch(
        "SELECT id FROM sessions WHERE owner_id = '' OR owner_id IS NULL"
    )
    for row in orphan_sessions:
        sid = row["id"]
        # Try to find the first human message sender in this session
        msg = await conn.fetchrow(
            "SELECT sender FROM messages WHERE session_id=$1 "
            "AND sender NOT IN ('system', 'Orchestrator', 'Architect', "
            "'CodeGen', 'Review', 'Test', 'Deploy', 'PM') "
            "ORDER BY created_at ASC LIMIT 1",
            sid,
        )
        owner_id = ""
        if msg:
            user_row = await conn.fetchrow(
                "SELECT id FROM users WHERE name=$1", msg["sender"]
            )
            if user_row:
                owner_id = user_row["id"]

        # Fallback: assign to admin
        if not owner_id:
            admin_row = await conn.fetchrow(
                "SELECT id FROM users WHERE role='admin' LIMIT 1"
            )
            if admin_row:
                owner_id = admin_row["id"]
            else:
                owner_id = DEFAULT_USER_ID

        await conn.execute(
            "UPDATE sessions SET owner_id=$1 WHERE id=$2", owner_id, sid
        )

        # Add owner as a session_member
        await conn.execute(
            "INSERT INTO session_members(session_id,user_id,role,joined_at) "
            "VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
            sid, owner_id, "owner", now_ts,
        )

    # 5. Make the default session public (so new users can see it)
    await conn.execute(
        "UPDATE sessions SET visibility='public' WHERE id=$1 AND visibility='private'",
        DEFAULT_SESSION_ID,
    )

    logger.info(
        "multi_user migration: %d sessions backfilled with owners",
        len(orphan_sessions),
    )


async def _migrate_agent_routes_pg(conn) -> None:
    """Add user_id column to agent_routes for per-user workflow isolation (idempotent)."""
    # 1. Add user_id column if not exists
    try:
        await conn.execute(
            "ALTER TABLE agent_routes ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT ''"
        )
    except Exception as exc:
        logger.warning("agent_routes user_id migration skipped: %s", exc)

    for migration in (
        "ALTER TABLE agent_routes ADD COLUMN IF NOT EXISTS edges_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE agent_routes ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE agent_routes ADD COLUMN IF NOT EXISTS schema_version INTEGER NOT NULL DEFAULT 1",
    ):
        try:
            await conn.execute(migration)
        except Exception as exc:
            logger.warning("agent_routes editor migration skipped: %s", exc)

    try:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS workflow_drafts (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                workflow_id INTEGER,
                draft_key TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                base_version INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, draft_key)
            )"""
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflow_drafts_user_updated "
            "ON workflow_drafts(user_id, updated_at DESC)"
        )
    except Exception as exc:
        logger.warning("workflow_drafts migration skipped: %s", exc)

    # 2. Drop old unique constraint on name (single-column)
    try:
        await conn.execute("ALTER TABLE agent_routes DROP CONSTRAINT IF EXISTS agent_routes_name_key")
    except Exception as exc:
        logger.warning("agent_routes drop name_key skipped: %s", exc)

    # 3. Create composite unique index (name, user_id) if not exists
    try:
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_routes_name_user ON agent_routes(name, user_id)"
        )
    except Exception as exc:
        logger.warning("agent_routes composite unique index skipped: %s", exc)

    # 4. Backfill existing routes without user_id with the default admin user
    try:
        await conn.execute(
            "UPDATE agent_routes SET user_id=$1 WHERE user_id='' OR user_id IS NULL",
            DEFAULT_USER_ID,
        )
    except Exception as exc:
        logger.warning("agent_routes backfill user_id skipped: %s", exc)
