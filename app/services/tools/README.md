# Tool execution infrastructure

`sandbox_executor.py` owns bounded subprocess execution; `streaming_executor.py`
routes queued calls through the canonical `app/services/tool_executor.py` gateway.

`receipts.py` owns the local tool receipt lifecycle. Production recovery uses
`SQLiteToolReceiptStore`: an immediate transaction admits one process per
idempotency key, then atomically persists terminal state and the complete
executor result. `ToolReceipt` exposes metadata only. The separate result body
is canonical UTF-8 JSON, limited to 1 MiB without truncation, with a SHA-256
digest and an optional post-execution workspace revision supplied at completion.
`app/services/recovery_receipts.py` validates bodies before recovery and keeps
their contents out of DTO representations.

STARTED and UNKNOWN outcomes require reconciliation and cannot be replayed.
Completed results cannot be replaced, except for an identical completion retry.
Historical successful receipts without result bodies, corrupt digests, invalid
JSON and oversized bodies fail recovery without repeating the handler or
overwriting the successful receipt. FAILED results can be read explicitly for
diagnosis, but the default recovery path admits only SUCCEEDED.

The JSON store remains a compatibility adapter for a single process; it does
not provide SQLite's cross-process claim guarantee. Receipt storage is private
local execution state and must not be included in public execution projections.

Verified by `tests/services/test_recovery_receipts.py`, including real process
competition, a process crash after a side effect, and recovery after process exit.
