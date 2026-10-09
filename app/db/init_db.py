from __future__ import annotations

import logging

from app.config import DEFAULT_SESSION_ID, DEFAULT_USER_ID  # noqa: F401 - compatibility exports
from app.db.legacy_migrations import (  # noqa: F401 - compatibility exports
    _migrate_agent_registry_pg,
    _migrate_agent_routes_pg,
    _migrate_multi_user_pg,
)
from app.db.legacy_schema import _PG_DDL  # noqa: F401 - compatibility export
from app.db.legacy_seeds import (  # noqa: F401 - compatibility exports
    _default_password_hash,
    _seed_agent_routes_pg,
    _seed_agents_pg,
    _seed_model_configs_pg,
    _seed_session_pg,
    _seed_templates_pg,
    _seed_users_pg,
    now,
)
from app.db.sqlite_translator import (  # noqa: F401 - compatibility exports
    _MISSION_CONTROL_PLANE_SQLITE_UPGRADES,
    _create_mission_control_plane_sqlite,
    _strip_check_constraints,
)

logger = logging.getLogger("agenthub.db.init")
SQLITE_SCHEMA_VERSION = 5

async def ainit_db() -> None:
    """Create all tables and seed data on the configured backend.

    Order: (1) Alembic migrations, (2) legacy DDL (idempotent fallback),
    (3) seed data.
    """
    from app.config import DB_BACKEND, DATABASE_URL

    if DB_BACKEND == "sqlite" or (DB_BACKEND == "auto" and not DATABASE_URL):
        await _ainit_sqlite()
    else:
        await _ainit_postgresql()


async def _ainit_sqlite() -> None:
    """Initialize the local profile without PostgreSQL-only migrations."""
    from app.db.sqlite_initializer import initialize_sqlite

    await initialize_sqlite()


async def _ainit_postgresql() -> None:
    """Create all tables and seed data on PostgreSQL."""
    from app.db.session import aget_pool

    pool = await aget_pool()
    if pool is None:
        raise RuntimeError("PostgreSQL pool not available — check DATABASE_URL")

    async with pool.acquire() as conn:
        # ── Step 1: Apply Alembic migrations ────────────────────────
        await _apply_alembic_migrations(conn)

        # ── Step 2: Legacy DDL (idempotent fallback for non-Alembic tables) ──
        for ddl in _PG_DDL:
            try:
                await conn.execute(ddl)
            except Exception as exc:
                logger.warning("init_db PG DDL failed: %s — %s", exc, ddl[:80])

        logger.info("init_db: PostgreSQL tables created (%d DDL statements)", len(_PG_DDL))

        # ── Step 3: Runtime migrations for existing databases ───────
        await _migrate_agent_registry_pg(conn)
        await _migrate_multi_user_pg(conn)
        await _migrate_agent_routes_pg(conn)

        # ── Step 4: Seed default data ───────────────────────────────
        await _seed_users_pg(conn)
        await _seed_session_pg(conn)
        await _seed_agents_pg(conn)
        await _seed_templates_pg(conn)
        await _seed_agent_routes_pg(conn)
        await _seed_model_configs_pg(conn)

        logger.info("init_db: PostgreSQL seed data inserted")


async def _apply_alembic_migrations(conn) -> None:
    """Apply the runtime-supported migration chain on the active connection."""
    from app.db.migrations.runner import apply_startup_migrations

    await apply_startup_migrations(conn, logger=logger)
