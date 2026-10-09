"""Durable, local tool-execution receipts used for idempotent recovery.

Receipt DTOs contain identifiers and outcome metadata only. Complete executor
results are stored separately as bounded canonical JSON for verified recovery;
arguments are never persisted. SQLite provides cross-process arbitration.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any

from app.services.recovery_receipts import (
    RecoveredToolResult,
    RecoveryReceiptError,
    decode_receipt_result,
    encode_receipt_result,
    receipt_result_status,
)


def _completion_payload(
    result: Mapping[str, Any],
    tool_name: str,
    revision_provider: Callable[[], str] | None,
) -> tuple[str, str, str, str | None]:
    if result.get("tool_name", tool_name) != tool_name:
        raise RecoveryReceiptError("tool receipt result belongs to another tool")
    status = receipt_result_status(result)
    body, digest = encode_receipt_result(result)
    revision = revision_provider() if revision_provider is not None else None
    if revision is not None and (not isinstance(revision, str) or not revision.strip()):
        raise RecoveryReceiptError("tool receipt workspace revision is invalid")
    return body, digest, status, revision


def _recover_payload(
    value: Mapping[str, Any],
    *,
    tool_name: str | None,
    allow_failed: bool,
) -> RecoveredToolResult:
    status = value.get("status")
    if status != "SUCCEEDED" and not (allow_failed and status == "FAILED"):
        raise RecoveryReceiptError("tool receipt has no completed recoverable result")
    if tool_name is not None and value.get("tool_name") != tool_name:
        raise RecoveryReceiptError("tool receipt belongs to another tool")
    recovered = decode_receipt_result(
        value.get("result_json"), value.get("result_digest"),
        value.get("post_workspace_revision"),
    )
    if receipt_result_status(recovered.result) != status:
        raise RecoveryReceiptError("tool receipt result status does not match")
    if recovered.result.get("tool_name", value.get("tool_name")) != value.get("tool_name"):
        raise RecoveryReceiptError("tool receipt result belongs to another tool")
    return recovered


def _same_completion(
    existing: Mapping[str, Any], body: str, digest: str, status: str,
    revision: str | None,
) -> bool:
    return (
        existing.get("status") == status
        and existing.get("result_json") == body
        and existing.get("result_digest") == digest
        and existing.get("post_workspace_revision") == revision
    )


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

    def __init__(
        self, path: Path, *,
        workspace_revision_provider: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._read_corrupt = False
        self._workspace_revision_provider = workspace_revision_provider

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
            existing = data.get(receipt.idempotency_key)
            if isinstance(existing, dict) and existing.get("status") in {"SUCCEEDED", "FAILED"}:
                raise RecoveryReceiptError("completed tool receipt cannot be overwritten")
            data[receipt.idempotency_key] = asdict(receipt) | {"status": receipt.status.value}
            self._write(data)

    def complete(
        self, key: str, tool_name: str, now: float, *, result: Mapping[str, Any],
    ) -> ToolReceipt:
        body, digest, status, revision = _completion_payload(
            result, tool_name, self._workspace_revision_provider
        )
        with self._lock:
            data = self._read()
            existing = data.get(key)
            if self._read_corrupt or not isinstance(existing, dict):
                raise RecoveryReceiptError("tool receipt completion requires STARTED")
            if existing.get("tool_name") != tool_name:
                raise RecoveryReceiptError("tool receipt belongs to another tool")
            if existing.get("status") != "STARTED":
                if _same_completion(existing, body, digest, status, revision):
                    return self.get(key)  # type: ignore[return-value]
                raise RecoveryReceiptError("completed or unknown tool receipt cannot be overwritten")
            error_type = None if status == "SUCCEEDED" else str(result.get("error_type") or "tool_failure")
            receipt = ToolReceipt(key, tool_name, ToolReceiptStatus(status), now, error_type, digest)
            data[key] = asdict(receipt) | {
                "status": status, "result_json": body,
                "post_workspace_revision": revision,
            }
            self._write(data)
            return receipt

    def recover_result(
        self, key: str, *, tool_name: str | None = None, allow_failed: bool = False,
    ) -> RecoveredToolResult:
        with self._lock:
            data = self._read()
            value = data.get(key)
            if self._read_corrupt or not isinstance(value, dict):
                raise RecoveryReceiptError("tool receipt has no recoverable result")
            return _recover_payload(value, tool_name=tool_name, allow_failed=allow_failed)

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

    def __init__(
        self, path: Path, *,
        workspace_revision_provider: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._workspace_revision_provider = workspace_revision_provider
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
        with self._transaction() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS tool_receipts (
                    idempotency_key TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    error_type TEXT,
                    result_digest TEXT,
                    result_json TEXT,
                    post_workspace_revision TEXT
                )"""
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tool_receipts)")}
            for column in ("result_json", "post_workspace_revision"):
                if column not in columns:
                    connection.execute(f"ALTER TABLE tool_receipts ADD COLUMN {column} TEXT")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

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
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT status, tool_name FROM tool_receipts WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                if row["tool_name"] != tool_name:
                    return "unknown_outcome"
                status = ToolReceiptStatus(str(row["status"]))
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
            return "execute"

    def put(self, receipt: ToolReceipt) -> None:
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT status FROM tool_receipts WHERE idempotency_key = ?",
                (receipt.idempotency_key,),
            ).fetchone()
            if existing is not None and existing["status"] in {"SUCCEEDED", "FAILED"}:
                raise RecoveryReceiptError("completed tool receipt cannot be overwritten")
            connection.execute(
                "INSERT INTO tool_receipts"
                "(idempotency_key, tool_name, status, updated_at, error_type, result_digest)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(idempotency_key) DO UPDATE SET"
                " tool_name=excluded.tool_name, status=excluded.status,"
                " updated_at=excluded.updated_at, error_type=excluded.error_type,"
                " result_digest=excluded.result_digest,"
                " result_json=NULL, post_workspace_revision=NULL",
                (receipt.idempotency_key, receipt.tool_name, receipt.status.value,
                 receipt.updated_at, receipt.error_type, receipt.result_digest),
            )

    def complete(
        self, key: str, tool_name: str, now: float, *, result: Mapping[str, Any],
    ) -> ToolReceipt:
        """Atomically finalize one claimed result, never replacing terminal truth."""
        body, digest, status, revision = _completion_payload(
            result, tool_name, self._workspace_revision_provider
        )
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM tool_receipts WHERE idempotency_key = ?", (key,),
            ).fetchone()
            if row is None:
                raise RecoveryReceiptError("tool receipt completion requires STARTED")
            if row["tool_name"] != tool_name:
                raise RecoveryReceiptError("tool receipt belongs to another tool")
            if row["status"] != "STARTED":
                if _same_completion(dict(row), body, digest, status, revision):
                    return self._from_row(row, key)  # type: ignore[return-value]
                raise RecoveryReceiptError("completed or unknown tool receipt cannot be overwritten")
            error_type = None if status == "SUCCEEDED" else str(result.get("error_type") or "tool_failure")
            connection.execute(
                "UPDATE tool_receipts SET status=?, updated_at=?, error_type=?,"
                " result_digest=?, result_json=?, post_workspace_revision=?"
                " WHERE idempotency_key=?",
                (status, now, error_type, digest, body, revision, key),
            )
            return ToolReceipt(key, tool_name, ToolReceiptStatus(status), now, error_type, digest)

    def recover_result(
        self, key: str, *, tool_name: str | None = None, allow_failed: bool = False,
    ) -> RecoveredToolResult:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM tool_receipts WHERE idempotency_key=?", (key,),
            ).fetchone()
        if row is None:
            raise RecoveryReceiptError("tool receipt has no recoverable result")
        return _recover_payload(dict(row), tool_name=tool_name, allow_failed=allow_failed)

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
