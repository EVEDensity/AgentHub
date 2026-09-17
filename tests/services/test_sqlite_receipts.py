from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

from app.services.tools.receipts import SQLiteToolReceiptStore


def _claim(path: str, barrier: multiprocessing.Barrier, output: multiprocessing.Queue) -> None:
    store = SQLiteToolReceiptStore(Path(path))
    barrier.wait()
    output.put(store.claim_started("m/w/1/file_write/abc", "file_write", time.time()))


def test_sqlite_receipt_claim_is_single_winner(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    output = context.Queue()
    path = str(tmp_path / "receipts.sqlite3")
    first = context.Process(target=_claim, args=(path, barrier, output))
    second = context.Process(target=_claim, args=(path, barrier, output))
    first.start()
    second.start()
    first.join(15)
    second.join(15)
    assert first.exitcode == 0
    assert second.exitcode == 0
    decisions = sorted([output.get(timeout=2), output.get(timeout=2)])
    assert decisions == ["execute", "unknown_outcome"]
