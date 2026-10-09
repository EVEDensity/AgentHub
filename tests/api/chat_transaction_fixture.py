"""Companion wiring for the legacy in-memory chat test repositories.

This fixture does not prove transaction isolation; real SQLite tests cover it.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace


def configure_pending_transaction(fakes: dict) -> None:
    pending = fakes["pending"]
    if hasattr(pending, "transaction"):
        return

    @asynccontextmanager
    async def transaction():
        yield SimpleNamespace(pendings=pending, missions=fakes["repo"],
                              session_events=fakes["session_events"], sessions=fakes["sessions"])

    pending.transaction = transaction
    pending.get_pending_for_update = pending.get_pending
