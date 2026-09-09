"""Durable, local tool-execution receipts used for idempotent recovery.

Receipts contain identifiers and outcome metadata only; arguments and tool
output are never persisted.  The store is intentionally file-backed so the
local CLI can recover after a process crash without requiring PostgreSQL.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any


class ToolReceiptStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ToolReceipt:
    idempotency_key: str
    tool_name: str
    status: ToolReceiptStatus
    updated_at: float
    error_type: str | None = None
    result_digest: str | None = None


class ToolReceiptStore:
    """Atomic JSON receipt store with fail-closed recovery semantics."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._read_corrupt = False

    def get(self, idempotency_key: str) -> ToolReceipt | None:
        with self._lock:
            data = self._read()
            if self._read_corrupt:
                return ToolReceipt(idempotency_key, "unknown", ToolReceiptStatus.UNKNOWN, 0.0, error_type="corrupt_receipt_store")
            value = data.get(idempotency_key)
            if not isinstance(value, dict):
                return None
            try:
                return ToolReceipt(
                    idempotency_key=idempotency_key,
                    tool_name=str(value["tool_name"]),
                    status=ToolReceiptStatus(str(value["status"])),
                    updated_at=float(value["updated_at"]),
                    error_type=value.get("error_type"),
                    result_digest=value.get("result_digest"),
                )
            except (KeyError, TypeError, ValueError):
                # A corrupt receipt must block replay rather than silently
                # allowing a side effect to run twice.
                return ToolReceipt(idempotency_key, "unknown", ToolReceiptStatus.UNKNOWN, 0.0)

    def put(self, receipt: ToolReceipt) -> None:
        with self._lock:
            data = self._read()
            if self._read_corrupt:
                # Never overwrite an unreadable journal; doing so could erase
                # evidence of an indeterminate side effect.
                raise RuntimeError("tool receipt store is corrupt; manual reconciliation required")
            data[receipt.idempotency_key] = asdict(receipt) | {"status": receipt.status.value}
            self._write(data)

    def mark_started(self, key: str, tool_name: str, now: float) -> ToolReceipt:
        receipt = ToolReceipt(key, tool_name, ToolReceiptStatus.STARTED, now)
        self.put(receipt)
        return receipt

    def mark_unknown(self, key: str, tool_name: str, now: float, *, error_type: str = "cancelled") -> ToolReceipt:
        """Persist an indeterminate outcome; recovery must reconcile manually."""
        receipt = ToolReceipt(key, tool_name, ToolReceiptStatus.UNKNOWN, now, error_type=error_type)
        self.put(receipt)
        return receipt

    def mark_failed(self, key: str, tool_name: str, now: float, *, error_type: str | None = None) -> ToolReceipt:
        """Persist a handled failure without permitting implicit replay."""
        receipt = ToolReceipt(key, tool_name, ToolReceiptStatus.FAILED, now, error_type=error_type)
        self.put(receipt)
        return receipt

    def replay_decision(self, key: str, *, strict: bool = False) -> str:
        """Return the replay decision for a prior call.

        ``strict=True`` is used by recovery gates and treats a corrupt journal
        as ``unknown_outcome``. The default remains lenient for legacy callers
        that use an absent/unreadable journal as a stateless execution mode.
        """
        receipt = self.get(key)
        if self._read_corrupt and not strict:
            return "execute"
        if receipt is None or receipt.status is ToolReceiptStatus.NOT_STARTED:
            return "execute"
        if receipt.status is ToolReceiptStatus.SUCCEEDED:
            return "already_succeeded"
        if receipt.status in {ToolReceiptStatus.UNKNOWN, ToolReceiptStatus.STARTED}:
            return "unknown_outcome"
        if receipt.status is ToolReceiptStatus.FAILED:
            return "previous_failure"
        return "unknown_outcome"

    def _read(self) -> dict[str, Any]:
        self._read_corrupt = False
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                self._read_corrupt = True
                return {}
            return value
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            self._read_corrupt = True
            return {}

    def _write(self, value: dict[str, Any]) -> None:
        fd, temp_name = tempfile.mkstemp(prefix="receipts-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=True, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


class SQLiteToolReceiptStore:
    """Multi-process local Receipt store backed by SQLite transactions.

    ``claim_started`` uses ``BEGIN IMMEDIATE`` and a primary-key row so only
    one process can acquire an absent idempotency key. Existing STARTED and
    UNKNOWN rows are returned as ``unknown_outcome`` and are never replayed.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path, timeout=10.0, isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS tool_receipts (
                    idempotency_key TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    error_type TEXT,
                    result_digest TEXT
                )"""
            )

    @staticmethod
    def _from_row(row: sqlite3.Row | None, key: str) -> ToolReceipt | None:
        if row is None:
            return None
        try:
            return ToolReceipt(
                idempotency_key=key,
                tool_name=str(row["tool_name"]),
                status=ToolReceiptStatus(str(row["status"])),
                updated_at=float(row["updated_at"]),
                error_type=row["error_type"],
                result_digest=row["result_digest"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("tool receipt database contains an invalid row") from exc

    def get(self, idempotency_key: str) -> ToolReceipt | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM tool_receipts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self._from_row(row, idempotency_key)

    def claim_started(self, key: str, tool_name: str, now: float) -> str:
        """Atomically claim a receipt row, returning a replay decision."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM tool_receipts WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                status = ToolReceiptStatus(str(row["status"]))
                connection.execute("COMMIT")
                if status is ToolReceiptStatus.SUCCEEDED:
                    return "already_succeeded"
                if status is ToolReceiptStatus.FAILED:
                    return "previous_failure"
                return "unknown_outcome"
            connection.execute(
                "INSERT INTO tool_receipts"
                "(idempotency_key, tool_name, status, updated_at) VALUES (?, ?, ?, ?)",
                (key, tool_name, ToolReceiptStatus.STARTED.value, now),
            )
            connection.execute("COMMIT")
            return "execute"
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def put(self, receipt: ToolReceipt) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO tool_receipts"
                "(idempotency_key, tool_name, status, updated_at, error_type, result_digest)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(idempotency_key) DO UPDATE SET"
                " tool_name=excluded.tool_name, status=excluded.status,"
                " updated_at=excluded.updated_at, error_type=excluded.error_type,"
                " result_digest=excluded.result_digest",
                (receipt.idempotency_key, receipt.tool_name, receipt.status.value,
                 receipt.updated_at, receipt.error_type, receipt.result_digest),
            )
            connection.execute("COMMIT")

    def mark_started(self, key: str, tool_name: str, now: float) -> ToolReceipt:
        decision = self.claim_started(key, tool_name, now)
        if decision != "execute":
            raise RuntimeError(f"receipt claim rejected: {decision}")
        return self.get(key) or ToolReceipt(key, tool_name, ToolReceiptStatus.STARTED, now)

    def mark_unknown(self, key: str, tool_name: str, now: float, *, error_type: str = "cancelled") -> ToolReceipt:
        receipt = ToolReceipt(key, tool_name, ToolReceiptStatus.UNKNOWN, now, error_type=error_type)
        self.put(receipt)
        return receipt

    def mark_failed(self, key: str, tool_name: str, now: float, *, error_type: str | None = None) -> ToolReceipt:
        receipt = ToolReceipt(key, tool_name, ToolReceiptStatus.FAILED, now, error_type=error_type)
        self.put(receipt)
        return receipt

    def replay_decision(self, key: str, *, strict: bool = False) -> str:
        receipt = self.get(key)
        if receipt is None or receipt.status is ToolReceiptStatus.NOT_STARTED:
            return "execute"
        if receipt.status is ToolReceiptStatus.SUCCEEDED:
            return "already_succeeded"
        if receipt.status is ToolReceiptStatus.FAILED:
            return "previous_failure"
        return "unknown_outcome"


__all__ = ["ToolReceipt", "ToolReceiptStatus", "ToolReceiptStore", "SQLiteToolReceiptStore"]
