"""Local database bootstrap and transactional incremental compatibility upgrades."""

from app.db.migrations.checkpoint_resume import upgrade_checkpoint_resume_sqlite
from app.db.migrations.session_workspace import upgrade_session_workspace_sqlite
from app.db.migrations.runner_presence import upgrade_runner_presence_sqlite


async def initialize_sqlite() -> None:
    from app.db import init_db as schema
    from app.db.session import aget_pool

    pool = await aget_pool()
    async with pool.acquire() as connection:
        version = await connection.fetchval("SELECT MAX(version) FROM schema_migrations")
        if version is not None and int(version) >= schema.SQLITE_SCHEMA_VERSION:
            return
        if version is not None and int(version) in {2, 3, 4}:
            async with connection.transaction():
                if int(version) == 2:
                    await upgrade_checkpoint_resume_sqlite(connection)
                if int(version) < 4:
                    await upgrade_session_workspace_sqlite(connection)
                await upgrade_runner_presence_sqlite(connection)
                await _stamp(connection, schema)
            schema.logger.info("init_db: SQLite compatibility schema upgraded")
            return
        await _bootstrap_legacy(connection, schema)
        async with connection.transaction():
            await schema._create_mission_control_plane_sqlite(connection)
            await upgrade_session_workspace_sqlite(connection)
            await upgrade_runner_presence_sqlite(connection)
            await _stamp(connection, schema)
    schema.logger.info("init_db: SQLite local database initialized")


async def _stamp(connection, schema) -> None:
    await connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES($1, $2)",
        schema.SQLITE_SCHEMA_VERSION, schema.now(),
    )


async def _bootstrap_legacy(connection, schema) -> None:
    for ddl in schema._PG_DDL:
        if ddl.strip().upper().startswith(("ALTER TABLE", "DO $$", "CREATE EXTENSION")):
            continue
        sqlite_ddl = (ddl.replace("SERIAL", "INTEGER").replace("BIGSERIAL", "INTEGER")
                      .replace("BOOLEAN", "INTEGER").replace("BYTEA", "BLOB"))
        try:
            await connection.execute(sqlite_ddl)
        except Exception as exc:  # noqa: BLE001 - historical fallback DDL, never state migrations
            schema.logger.warning("init_db SQLite DDL skipped: %s — %s", exc, ddl[:80])
    for seed in (
        schema._seed_users_pg, schema._seed_session_pg, schema._seed_agents_pg,
        schema._seed_templates_pg, schema._seed_agent_routes_pg, schema._seed_model_configs_pg,
    ):
        await seed(connection)
