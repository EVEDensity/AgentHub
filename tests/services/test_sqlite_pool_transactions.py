"""Concurrency contract of the SQLitePool transaction adapter.

The desktop profile serves every caller from one serialized SQLite
connection. Nested scopes in the same task reuse the transaction; other
tasks must wait until it is committed or rolled back before accessing it.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db.sqlite_pool import SQLitePool


class SQLitePoolTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_returning_write_commits_without_an_explicit_transaction(self) -> None:
        async with self._pool.acquire() as conn:
            await self._create_items(conn)
            row = await conn.fetchrow("INSERT INTO items VALUES(1,'returning') RETURNING id")
            self.assertEqual(row, {"id": 1})
            self.assertEqual(self._committed_rows(), 1)
            async with conn.transaction():
                await conn.execute("INSERT INTO items VALUES(2,'next transaction')")
        self.assertEqual(self._committed_rows(), 2)

    async def test_cancelled_implicit_writes_roll_back_before_releasing_connection(self) -> None:
        async with self._pool.acquire() as conn:
            await self._create_items(conn)
            for method in (conn.execute, conn.fetchrow):
                entered = asyncio.Event()
                original = conn._run

                async def delayed(operation, *args):
                    result = await original(operation, *args)
                    if args and str(args[0]).startswith("INSERT"):
                        entered.set()
                        await asyncio.Event().wait()
                    return result

                with patch.object(conn, "_run", new=delayed):
                    worker = asyncio.create_task(method("INSERT INTO items VALUES(1,'cancelled') RETURNING id"))
                    await asyncio.wait_for(entered.wait(), 2)
                    worker.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await worker
                self.assertEqual(self._committed_rows(), 0)
                async with conn.transaction():
                    self.assertEqual(await conn.fetchval("SELECT COUNT(*) FROM items"), 0)

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

    async def test_overlapping_tasks_use_separate_committed_transactions(self) -> None:
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
            # A different task waits for the first transaction's commit.
            async with conn.transaction():
                self.assertEqual(self._committed_rows(), 1)
                await conn.execute(
                    "INSERT INTO items(id, name) VALUES($1, $2)", 2, "inner"
                )

        async with self._pool.acquire() as first:
            await self._create_items(first)
            async with self._pool.acquire() as second:
                await asyncio.gather(hold_outer(first), join_inner(second))

        self.assertEqual(self._committed_rows(), 2)

    async def test_failure_in_one_task_does_not_rollback_another(self) -> None:
        entered = asyncio.Event()

        async def fail_first(conn):
            async with conn.transaction():
                await conn.execute("INSERT INTO items VALUES(1,'rollback')")
                entered.set()
                await asyncio.sleep(0.05)
                raise RuntimeError("first failed")

        async def succeed_second(conn):
            await entered.wait()
            async with conn.transaction():
                await conn.execute("INSERT INTO items VALUES(2,'commit')")

        async with self._pool.acquire() as first, self._pool.acquire() as second:
            await self._create_items(first)
            results = await asyncio.gather(fail_first(first), succeed_second(second), return_exceptions=True)
            self.assertIsInstance(results[0], RuntimeError)
            self.assertIsNone(results[1])
            self.assertEqual(await first.fetch("SELECT id,name FROM items"), [{"id": 2, "name": "commit"}])
        self.assertEqual(self._committed_rows(), 1)

    async def test_nontransaction_write_does_not_join_another_tasks_rollback(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def fail_transaction(conn):
            async with conn.transaction():
                await conn.execute("INSERT INTO items VALUES(1,'rollback')")
                entered.set()
                await release.wait()
                raise RuntimeError("rollback owner")

        async with self._pool.acquire() as first, self._pool.acquire() as second:
            await self._create_items(first)
            owner = asyncio.create_task(fail_transaction(first))
            await entered.wait()
            independent = asyncio.create_task(second.execute("INSERT INTO items VALUES(2,'independent')"))
            await asyncio.sleep(0)
            self.assertFalse(independent.done())
            self.assertEqual(self._committed_rows(), 0)
            release.set()
            await asyncio.gather(owner, independent, return_exceptions=True)
            self.assertEqual(await second.fetch("SELECT id,name FROM items"), [{"id": 2, "name": "independent"}])
        self.assertEqual(self._committed_rows(), 1)

    async def test_child_task_reads_wait_for_parent_transaction(self) -> None:
        async with self._pool.acquire() as first, self._pool.acquire() as second:
            await self._create_items(first)
            async with first.transaction():
                await first.execute("INSERT INTO items VALUES(1,'owned')")
                child = asyncio.create_task(second.fetchval("SELECT COUNT(*) FROM items"))
                await asyncio.sleep(0)
                self.assertFalse(child.done())
            self.assertEqual(await asyncio.wait_for(child, 1), 1)

    async def test_cancelled_owner_rolls_back_and_releases_waiting_transaction(self) -> None:
        entered = asyncio.Event()

        async def hold(conn):
            async with conn.transaction():
                await conn.execute("INSERT INTO items VALUES(1,'cancelled')")
                entered.set()
                await asyncio.Event().wait()

        async def next_transaction(conn):
            async with conn.transaction():
                await conn.execute("INSERT INTO items VALUES(2,'after-cancel')")

        async with self._pool.acquire() as first, self._pool.acquire() as second:
            await self._create_items(first)
            owner = asyncio.create_task(hold(first))
            await entered.wait()
            waiter = asyncio.create_task(next_transaction(second))
            await asyncio.sleep(0)
            self.assertFalse(waiter.done())
            owner.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await owner
            await asyncio.wait_for(waiter, 1)
            self.assertEqual(await first.fetch("SELECT id,name FROM items"), [{"id": 2, "name": "after-cancel"}])
        self.assertEqual(self._committed_rows(), 1)

    async def test_cancelled_waiter_does_not_release_another_tasks_transaction(self) -> None:
        async with self._pool.acquire() as first, self._pool.acquire() as second:
            await self._create_items(first)
            async with first.transaction():
                await first.execute("INSERT INTO items VALUES(1,'owner')")
                waiter = asyncio.create_task(second._begin())
                await asyncio.sleep(0)
                waiter.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await waiter
                await first.execute("INSERT INTO items VALUES(2,'still-owned')")
                self.assertEqual(self._committed_rows(), 0)
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

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO items(id, name) VALUES($1, $2)", 3, "fresh"
            )

        self.assertEqual(self._committed_rows(), 1)
