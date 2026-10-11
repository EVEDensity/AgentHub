from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from app.services.recovery_receipts import (
    MAX_RECEIPT_RESULT_BYTES,
    RecoveryReceiptError,
    encode_receipt_result,
)
from app.services.tool_executor import ToolExecutor
from app.services.tools.receipts import SQLiteToolReceiptStore, ToolReceipt, ToolReceiptStatus

KEY = "mission/work/1/file_write/args-digest"
TOOL = "file_write"
REVISION = "sha256:" + "f" * 64
RESULT = {"success": True, "tool_name": TOOL, "result": {"text": "真实结果 secret-body"}}


def _executor_process(
    database: str, output_file: str, barrier: Any, release: Any, output: Any,
) -> None:
    store = SQLiteToolReceiptStore(Path(database))
    executor = ToolExecutor()
    executor.configure(receipt_store=store)

    async def write(_: Any) -> dict[str, Any]:
        with Path(output_file).open("a", encoding="utf-8") as handle:
            handle.write("executed\n")
        output.put(("handler", None))
        if not release.wait(10):
            raise RuntimeError("test did not release handler")
        return dict(RESULT)

    barrier.wait(10)
    result = asyncio.run(executor.execute_callable(TOOL, {}, write, idempotency_key=KEY))
    output.put(("result", result))


def _crash_after_side_effect(database: str, output_file: str) -> None:
    store = SQLiteToolReceiptStore(Path(database))
    store.mark_started(KEY, TOOL, 1.0)
    Path(output_file).write_text("executed", encoding="utf-8")
    os._exit(23)


def _complete_in_process(database: str) -> None:
    store = SQLiteToolReceiptStore(Path(database), workspace_revision_provider=lambda: REVISION)
    store.mark_started(KEY, TOOL, 1.0)
    store.complete(KEY, TOOL, 2.0, result=RESULT)


def _stop_processes(processes: list[Any], release: Any) -> None:
    release.set()
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_competing_executor_processes_execute_side_effect_once(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    barrier, release, output = context.Barrier(2), context.Event(), context.Queue()
    database, output_file = tmp_path / "receipts.sqlite3", tmp_path / "output.txt"
    processes = [
        context.Process(target=_executor_process, args=(str(database), str(output_file), barrier, release, output))
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        initial = [output.get(timeout=15), output.get(timeout=15)]
        assert sorted(item[0] for item in initial) == ["handler", "result"]
        denied = next(item[1] for item in initial if item[0] == "result")
        assert denied["success"] is False
        assert denied["error_type"] == "idempotency"
        release.set()
        phase, result = output.get(timeout=15)
        assert phase == "result" and result["success"] is True
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert output_file.read_text(encoding="utf-8") == "executed\n"
        assert SQLiteToolReceiptStore(database).recover_result(KEY).result["result"] == RESULT["result"]
    finally:
        _stop_processes(processes, release)
        output.close()


def test_started_process_crash_never_replays_handler(tmp_path: Path) -> None:
    database, output_file = tmp_path / "receipts.sqlite3", tmp_path / "output.txt"
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_after_side_effect, args=(str(database), str(output_file)),
    )
    process.start()
    process.join(15)
    assert process.exitcode == 23
    store = SQLiteToolReceiptStore(database)
    executor = ToolExecutor()
    executor.configure(receipt_store=store)

    async def forbidden(_: Any) -> None:
        raise AssertionError("crashed side effect was replayed")

    result = asyncio.run(executor.execute_callable(TOOL, {}, forbidden, idempotency_key=KEY))
    assert result["success"] is False and result["recovered"] is False
    assert store.get(KEY).status is ToolReceiptStatus.STARTED
    assert output_file.read_text(encoding="utf-8") == "executed"


def test_success_survives_process_exit_and_returns_real_result(tmp_path: Path) -> None:
    database = tmp_path / "receipts.sqlite3"
    process = multiprocessing.get_context("spawn").Process(target=_complete_in_process, args=(str(database),))
    process.start()
    process.join(15)
    assert process.exitcode == 0
    store = SQLiteToolReceiptStore(database)
    recovered = store.recover_result(KEY)
    assert recovered.result == RESULT
    assert recovered.post_workspace_revision == REVISION
    assert "secret-body" not in repr(recovered)
    assert "secret-body" not in repr(store.get(KEY))
    executor = ToolExecutor()
    executor.configure(receipt_store=store)

    async def forbidden(_: Any) -> None:
        raise AssertionError("successful side effect was repeated")

    result = asyncio.run(executor.execute_callable(TOOL, {}, forbidden, idempotency_key=KEY))
    assert result == RESULT | {"recovered": True}


def test_completion_is_atomic_when_update_fails(tmp_path: Path) -> None:
    store = SQLiteToolReceiptStore(tmp_path / "receipts.sqlite3")
    store.mark_started(KEY, TOOL, 1.0)
    with sqlite3.connect(store.path) as connection:
        connection.execute("CREATE TRIGGER fail_complete BEFORE UPDATE ON tool_receipts BEGIN SELECT RAISE(ABORT, 'disk-write-failed'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.complete(KEY, TOOL, 2.0, result=RESULT)
    with sqlite3.connect(store.path) as connection:
        status, body, digest = connection.execute("SELECT status, result_json, result_digest FROM tool_receipts").fetchone()
    assert (status, body, digest) == ("STARTED", None, None)


def test_same_completion_is_idempotent_and_terminal_truth_is_immutable(tmp_path: Path) -> None:
    store = SQLiteToolReceiptStore(tmp_path / "receipts.sqlite3", workspace_revision_provider=lambda: REVISION)
    store.mark_started(KEY, TOOL, 1.0)
    first = store.complete(KEY, TOOL, 2.0, result=RESULT)
    assert store.complete(KEY, TOOL, 3.0, result=RESULT) == first
    with pytest.raises(RecoveryReceiptError, match="cannot be overwritten"):
        store.complete(KEY, TOOL, 4.0, result=RESULT | {"result": "different"})
    with pytest.raises(RecoveryReceiptError, match="cannot be overwritten"):
        store.mark_failed(KEY, TOOL, 5.0)
    assert store.recover_result(KEY).result == RESULT
    store.mark_unknown("unknown-key", TOOL, 1.0)
    with pytest.raises(RecoveryReceiptError, match="cannot be overwritten"):
        store.complete("unknown-key", TOOL, 2.0, result=RESULT)


def test_result_size_limit_counts_utf8_bytes_and_does_not_truncate(tmp_path: Path) -> None:
    store = SQLiteToolReceiptStore(tmp_path / "receipts.sqlite3")
    store.mark_started(KEY, TOOL, 1.0)
    base = {"success": True, "result": ""}
    body, _ = encode_receipt_result(base)
    bounded = base | {"result": "x" * (MAX_RECEIPT_RESULT_BYTES - len(body.encode("utf-8")))}
    assert len(encode_receipt_result(bounded)[0].encode("utf-8")) == MAX_RECEIPT_RESULT_BYTES
    store.complete(KEY, TOOL, 2.0, result=bounded)
    assert store.recover_result(KEY).result == bounded
    store.mark_started("oversized", TOOL, 1.0)
    with pytest.raises(RecoveryReceiptError, match="size limit"):
        store.complete("oversized", TOOL, 2.0, result={"success": True, "result": "界" * (MAX_RECEIPT_RESULT_BYTES // 3)})
    assert store.get("oversized").status is ToolReceiptStatus.STARTED


@pytest.mark.parametrize("bad_result", [{"result": float("nan")}, {"result": {1: "value"}}, {"result": object()}])
def test_invalid_result_cannot_finalize_started_receipt(tmp_path: Path, bad_result: Any) -> None:
    store = SQLiteToolReceiptStore(tmp_path / "receipts.sqlite3")
    store.mark_started(KEY, TOOL, 1.0)
    with pytest.raises(RecoveryReceiptError):
        store.complete(KEY, TOOL, 2.0, result=bad_result)
    assert store.get(KEY).status is ToolReceiptStatus.STARTED


@pytest.mark.parametrize("corruption", ["missing", "digest", "unicode_digest", "oversized", "noncanonical", "status"])
def test_corrupt_success_is_refused_without_reexecution_or_overwrite(tmp_path: Path, corruption: str) -> None:
    store = SQLiteToolReceiptStore(tmp_path / "receipts.sqlite3")
    store.mark_started(KEY, TOOL, 1.0)
    store.complete(KEY, TOOL, 2.0, result=RESULT)
    with sqlite3.connect(store.path) as connection:
        if corruption == "missing":
            connection.execute("UPDATE tool_receipts SET result_json=NULL")
        elif corruption in {"digest", "unicode_digest"}:
            bad_digest = "sha256:wrong" if corruption == "digest" else "sha256:损坏"
            connection.execute("UPDATE tool_receipts SET result_digest=?", (bad_digest,))
        else:
            bodies = {
                "oversized": "x" * (MAX_RECEIPT_RESULT_BYTES + 1),
                "noncanonical": '{"success": true}',
                "status": '{"success":false}',
            }
            body = bodies[corruption]
            digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
            connection.execute("UPDATE tool_receipts SET result_json=?, result_digest=?", (body, digest))
    before = store.get(KEY)
    executor = ToolExecutor()
    executor.configure(receipt_store=store)

    async def forbidden(_: Any) -> None:
        raise AssertionError("corrupt successful receipt was replayed")

    result = asyncio.run(executor.execute_callable(TOOL, {}, forbidden, idempotency_key=KEY))
    assert result["success"] is False and result["error_type"] == "receipt_recovery"
    assert store.get(KEY) == before
    assert store.get(KEY).status is ToolReceiptStatus.SUCCEEDED


def test_old_metadata_only_success_is_not_synthetic_success(tmp_path: Path) -> None:
    path = tmp_path / "receipts.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE tool_receipts (idempotency_key TEXT PRIMARY KEY, tool_name TEXT NOT NULL, status TEXT NOT NULL, updated_at REAL NOT NULL, error_type TEXT, result_digest TEXT)")
        connection.execute("INSERT INTO tool_receipts VALUES (?, ?, 'SUCCEEDED', 1.0, NULL, 'legacy-digest')", (KEY, TOOL))
    store = SQLiteToolReceiptStore(path)
    with pytest.raises(RecoveryReceiptError, match="no recoverable result"):
        store.recover_result(KEY)
    assert store.get(KEY) == ToolReceipt(KEY, TOOL, ToolReceiptStatus.SUCCEEDED, 1.0, result_digest="legacy-digest")


def test_failed_result_is_explicitly_readable_but_not_successfully_replayable(tmp_path: Path) -> None:
    store = SQLiteToolReceiptStore(tmp_path / "receipts.sqlite3")
    store.mark_started(KEY, TOOL, 1.0)
    failure = {"success": False, "tool_name": TOOL, "error": "real handler failure", "error_type": "runtime"}
    store.complete(KEY, TOOL, 2.0, result=failure)
    assert store.replay_decision(KEY, strict=True) == "previous_failure"
    with pytest.raises(RecoveryReceiptError, match="no completed recoverable result"):
        store.recover_result(KEY)
    assert store.recover_result(KEY, allow_failed=True).result == failure


def test_executor_persist_failure_does_not_claim_handled_tool_failure(tmp_path: Path) -> None:
    store = SQLiteToolReceiptStore(tmp_path / "receipts.sqlite3")
    executor = ToolExecutor()
    executor.configure(receipt_store=store)
    calls = 0

    async def oversized(_: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"success": True, "result": "x" * MAX_RECEIPT_RESULT_BYTES}

    first = asyncio.run(executor.execute_callable(TOOL, {}, oversized, idempotency_key=KEY))
    assert first["success"] is False and first["error_type"] == "receipt_persistence"
    assert store.get(KEY).status is ToolReceiptStatus.STARTED
    second = asyncio.run(executor.execute_callable(TOOL, {}, oversized, idempotency_key=KEY))
    assert second["success"] is False and second["error_type"] == "idempotency"
    assert calls == 1


def test_revision_provider_only_runs_for_completion(tmp_path: Path) -> None:
    calls = []

    def revision() -> str:
        calls.append(True)
        return REVISION

    store = SQLiteToolReceiptStore(tmp_path / "receipts.sqlite3", workspace_revision_provider=revision)
    store.mark_started(KEY, TOOL, 1.0)
    store.get(KEY)
    store.replay_decision(KEY, strict=True)
    assert calls == []
    store.complete(KEY, TOOL, 2.0, result=RESULT)
    assert store.recover_result(KEY).post_workspace_revision == REVISION
    assert calls == [True]


def test_receipt_preserves_complete_result_before_model_result_budget(tmp_path: Path) -> None:
    store = SQLiteToolReceiptStore(tmp_path / "receipts.sqlite3")

    class TruncateForModel:
        def process(self, result: dict[str, Any]) -> dict[str, Any]:
            result["result"] = "truncated for model"
            return result

    executor = ToolExecutor()
    executor.configure(receipt_store=store, result_storage=TruncateForModel())

    async def handler(_: Any) -> dict[str, Any]:
        return dict(RESULT)

    result = asyncio.run(executor.execute_callable(TOOL, {}, handler, idempotency_key=KEY))
    assert result["result"] == "truncated for model"
    assert store.recover_result(KEY).result["result"] == RESULT["result"]


def test_slow_revision_provider_does_not_block_event_loop_heartbeat(tmp_path: Path) -> None:
    release = threading.Event()
    provider_observed_heartbeat = []

    def revision() -> str:
        provider_observed_heartbeat.append(release.wait(3))
        return REVISION

    store = SQLiteToolReceiptStore(tmp_path / "receipts.sqlite3", workspace_revision_provider=revision)
    executor = ToolExecutor()
    executor.configure(receipt_store=store)

    async def handler(_: Any) -> dict[str, Any]:
        return dict(RESULT)

    async def heartbeat() -> None:
        await asyncio.sleep(.02)
        release.set()

    async def run() -> dict[str, Any]:
        result, _ = await asyncio.gather(
            executor.execute_callable(TOOL, {}, handler, idempotency_key=KEY), heartbeat(),
        )
        return result

    assert asyncio.run(run())["success"] is True
    assert provider_observed_heartbeat == [True]


def test_cancellation_during_completion_preserves_real_terminal_result(tmp_path: Path) -> None:
    started, release = threading.Event(), threading.Event()

    def revision() -> str:
        started.set()
        if not release.wait(3):
            raise RuntimeError("test completion was not released")
        return REVISION

    store = SQLiteToolReceiptStore(tmp_path / "receipts.sqlite3", workspace_revision_provider=revision)
    executor = ToolExecutor()
    executor.configure(receipt_store=store)
    calls = []

    async def handler(_: Any) -> dict[str, Any]:
        calls.append(True)
        return dict(RESULT)

    async def run() -> None:
        task = asyncio.create_task(executor.execute_callable(TOOL, {}, handler, idempotency_key=KEY))
        await asyncio.to_thread(started.wait, 3)
        assert started.is_set()
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done()
        assert store.get(KEY).status is ToolReceiptStatus.STARTED
        duplicate = await executor.execute_callable(TOOL, {}, handler, idempotency_key=KEY)
        assert duplicate["success"] is False and duplicate["error_type"] == "idempotency"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert calls == [True]
    assert store.get(KEY).status is ToolReceiptStatus.SUCCEEDED
    assert store.recover_result(KEY).result["result"] == RESULT["result"]
    assert store.recover_result(KEY).post_workspace_revision == REVISION


def test_failed_terminal_receipt_cannot_be_overwritten_by_legacy_put(tmp_path: Path) -> None:
    store = SQLiteToolReceiptStore(tmp_path / "receipts.sqlite3")
    store.mark_started(KEY, TOOL, 1.0)
    failure = {"success": False, "error": "real failure"}
    original = store.complete(KEY, TOOL, 2.0, result=failure)
    with pytest.raises(RecoveryReceiptError, match="cannot be overwritten"):
        store.put(ToolReceipt(KEY, TOOL, ToolReceiptStatus.SUCCEEDED, 3.0))
    assert store.get(KEY) == original
    assert store.recover_result(KEY, allow_failed=True).result == failure
