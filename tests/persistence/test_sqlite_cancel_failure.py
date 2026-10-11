import asyncio
import sqlite3
import threading

import pytest

from app.db.sqlite_pool import SQLiteConnection, SQLitePool


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_error", [sqlite3.OperationalError, RuntimeError])
async def test_cancellation_wins_over_failed_thread_and_waits_for_its_completion(worker_error):
    started = threading.Event()
    finish = threading.Event()

    def failed_worker():
        started.set()
        assert finish.wait(3)
        raise worker_error("database worker failed after cancellation")

    task = asyncio.create_task(SQLiteConnection._run(failed_worker))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(.01)
    task.cancel()
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_failed_cancelled_sqlite_operation_releases_gate_only_after_rollback(tmp_path):
    pool = SQLitePool(tmp_path / "cancel.sqlite3")
    await pool.initialize()
    started, finish = threading.Event(), threading.Event()

    def sql_failure():
        started.set()
        assert finish.wait(3)
        raise sqlite3.OperationalError("failure")

    try:
        async with pool.acquire() as connection:
            connection._connection.create_function("slow_failure", 0, sql_failure)
            task = asyncio.create_task(connection.fetch("SELECT slow_failure()"))
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            contender = asyncio.create_task(connection.fetchval("SELECT 42"))
            await asyncio.sleep(.01)
            assert not contender.done()
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await contender == 42
            assert not connection._connection.in_transaction
    finally:
        finish.set()
        await pool.close()
