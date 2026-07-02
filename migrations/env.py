"""Alembic migrations environment for AgentHub (PostgreSQL / asyncpg).

For offline mode (--sql), generates SQL to stdout.
For online mode, connects via a synchronous psycopg2 engine.
For revision --autogenerate, requires a live database to introspect.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# Alembic Config object
config = context.config

# Set up Python logging from alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve DATABASE_URL from environment, inject into config
_db_url = os.getenv("DATABASE_URL", "")
if _db_url:
    # asyncpg uses postgresql:// which is compatible with psycopg2
    config.set_main_option("sqlalchemy.url", _db_url)

# No SQLAlchemy MetaData — we author migrations as raw SQL for asyncpg.
target_metadata = None


def run_migrations_offline() -> None:
    """Generate SQL without connecting to the database (--sql mode)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url or "postgresql://localhost:5432/agenthub",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")

    connectable = create_engine(
        url,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
