from __future__ import annotations

import asyncio

from app.db.sqlite_pool import SQLitePool


def test_sqlite_pool_supports_postgres_placeholders_and_transactions(tmp_path):
    async def scenario():
        pool = SQLitePool(tmp_path / "agenthub.db")
        await pool.initialize()
        async with pool.acquire() as conn:
            await conn.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            await conn.execute("INSERT INTO items(id, name) VALUES($1, $2)", 1, "local")
            row = await conn.fetchrow("SELECT name FROM items WHERE id=$1", 1)
            assert row == {"name": "local"}
            async with conn.transaction():
                await conn.execute("INSERT INTO items(id, name) VALUES($1, $2)", 2, "tx")
        await pool.close()

    asyncio.run(scenario())


def test_sqlite_pool_initializes_wal_journal_and_normal_synchronous(tmp_path):
    """P3-2a: the local profile runs WAL + synchronous=NORMAL (idempotent)."""
    db_path = tmp_path / "agenthub.db"

    async def scenario():
        pool = SQLitePool(db_path)
        await pool.initialize()
        async with pool.acquire() as conn:
            journal_mode = await conn.fetchval("PRAGMA journal_mode")
            synchronous = await conn.fetchval("PRAGMA synchronous")
        await pool.close()
        return journal_mode, synchronous

    journal_mode, synchronous = asyncio.run(scenario())
    assert str(journal_mode).lower() == "wal"
    assert int(synchronous) == 1  # 1 = NORMAL

    # Re-initializing the same database keeps WAL (idempotent pragma).
    journal_mode_again, _ = asyncio.run(scenario())
    assert str(journal_mode_again).lower() == "wal"


def test_local_database_initialization_creates_schema_and_seed_data(tmp_path, monkeypatch):
    import app.config as config
    import app.db.session as session
    from app.db.init_db import ainit_db

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "SQLITE_PATH", tmp_path / "agenthub.db")
    monkeypatch.setattr(session, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(session, "SQLITE_PATH", tmp_path / "agenthub.db")
    session._sqlite_pool = None

    async def scenario():
        await ainit_db()
        pool = await session.aget_pool()
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT COUNT(*) FROM users") >= 1
            assert await conn.fetchval("SELECT COUNT(*) FROM schema_migrations") == 1
        await session.aclose_pool()

    asyncio.run(scenario())
