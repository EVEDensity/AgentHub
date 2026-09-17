from __future__ import annotations

from pathlib import Path

from app.services.tools.receipts import (
    ToolReceipt,
    ToolReceiptStatus,
    ToolReceiptStore,
)


def test_receipts_are_atomic_and_block_unknown_replay(tmp_path: Path) -> None:
    store = ToolReceiptStore(tmp_path / "receipts.json")
    key = "mission/work/1/call-1"

    assert store.replay_decision(key) == "execute"
    store.mark_started(key, "file_write", 1.0)
    assert store.replay_decision(key) == "unknown_outcome"

    store.put(ToolReceipt(key, "file_write", ToolReceiptStatus.SUCCEEDED, 2.0, result_digest="abc"))
    assert store.get(key) is not None
    assert store.replay_decision(key) == "already_succeeded"


def test_corrupt_receipt_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "receipts.json"
    path.write_text("{not-json", encoding="utf-8")
    store = ToolReceiptStore(path)
    assert store.replay_decision("m/w/1/c") == "execute"
    path.write_text('{"m/w/1/c": {"status": "BROKEN"}}', encoding="utf-8")
    assert store.replay_decision("m/w/1/c") == "unknown_outcome"


def test_corrupt_receipt_store_blocks_strict_writes(tmp_path: Path) -> None:
    path = tmp_path / "receipts.json"
    path.write_text("not-json", encoding="utf-8")
    store = ToolReceiptStore(path)
    assert store.replay_decision("m/w/1/c", strict=True) == "unknown_outcome"
    try:
        store.mark_started("m/w/1/c", "shell", 1.0)
    except RuntimeError as exc:
        assert "corrupt" in str(exc)
    else:
        raise AssertionError("corrupt receipt journal was overwritten")
