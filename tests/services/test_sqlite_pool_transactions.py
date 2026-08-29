"""Concurrency contract of the SQLitePool transaction adapter.

The desktop profile serves every caller from one serialized SQLite
connection. Transaction scopes may overlap (different coroutines) or nest
(same coroutine) on that connection, so a second BEGIN must reuse the open
transaction instead of raising "cannot start a transaction within a
transaction".
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db.sqlite_pool import SQLitePool


class SQLitePoolTransactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._pool = SQLitePool(Path(tmp.name) / "agenthub.db")

    async def asyncSetUp(self) -> None:
        await self._pool.initialize()
        self.addCleanup(self._pool.close)

    async def _create_items(self, conn) -> None:
        await conn.execute(
            "CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )

    def _committed_rows(self) -> int:
        """Read through a separate raw connection so only committed rows count."""
        connection = sqlite3.connect(self._pool.path)
        try:
            return int(
                connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            )
        finally:
            connection.close()

    async def test_nested_transaction_contexts_reuse_one_begin(self) -> None:
        async with self._pool.acquire() as conn:
            await self._create_items(conn)
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO items(id, name) VALUES($1, $2)", 1, "outer"
                )
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO items(id, name) VALUES($1, $2)", 2, "inner"
                    )
                await conn.execute(
                    "INSERT INTO items(id, name) VALUES($1, $2)", 3, "after-inner"
                )

        self.assertEqual(self._committed_rows(), 3)

    async def test_overlapping_transaction_contexts_reuse_one_begin(self) -> None:
        entered = asyncio.Event()

        async def hold_outer(conn) -> None:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO items(id, name) VALUES($1, $2)", 1, "outer"
                )
                entered.set()
                await asyncio.sleep(0.05)

        async def join_inner(conn) -> None:
            await entered.wait()
            # Second BEGIN on the shared connection while the outer scope is
            # still open must reuse the transaction, not raise.
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO items(id, name) VALUES($1, $2)", 2, "inner"
                )

        async with self._pool.acquire() as first:
            await self._create_items(first)
            async with self._pool.acquire() as second:
                await asyncio.gather(hold_outer(first), join_inner(second))

        self.assertEqual(self._committed_rows(), 2)

    async def test_transaction_state_resets_after_commit(self) -> None:
        async with self._pool.acquire() as conn:
            await self._create_items(conn)
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO items(id, name) VALUES($1, $2)", 1, "tx"
                )

            # Outside any transaction scope, statements auto-commit again.
            await conn.execute("INSERT INTO items(id, name) VALUES($1, $2)", 2, "auto")
            self.assertEqual(self._committed_rows(), 2)

            # A fresh transaction scope opens and commits cleanly.
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO items(id, name) VALUES($1, $2)", 3, "second-tx"
                )

        self.assertEqual(self._committed_rows(), 3)

    async def test_rollback_path_leaves_connection_reusable(self) -> None:
        async with self._pool.acquire() as conn:
            await self._create_items(conn)
            with self.assertRaises(RuntimeError):
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO items(id, name) VALUES($1, $2)", 1, "doomed"
                    )
                    raise RuntimeError("boom")

            self.assertEqual(self._committed_rows(), 0)

            await conn.execute("INSERT INTO items(id, name) VALUES($1, $2)", 2, "auto")
            self.assertEqual(self._committed_rows(), 1)

            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO items(id, name) VALUES($1, $2)", 3, "fresh"
                )

        self.assertEqual(self._committed_rows(), 2)

    async def test_inner_failure_rolls_back_the_shared_transaction(self) -> None:
        async with self._pool.acquire() as conn:
            await self._create_items(conn)
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO items(id, name) VALUES($1, $2)", 1, "outer"
                )
                with self.assertRaises(RuntimeError):
                    async with conn.transaction():
                        await conn.execute(
                            "INSERT INTO items(id, name) VALUES($1, $2)", 2, "inner"
                        )
                        raise RuntimeError("inner failure")
                # The outer scope itself does not fail, but the failed inner
                # scope must poison the shared transaction.

        self.assertEqual(self._committed_rows(), 0)

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO items(id, name) VALUES($1, $2)", 3, "fresh"
                )

        self.assertEqual(self._committed_rows(), 1)
