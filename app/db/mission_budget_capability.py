"""Reject the known stateless SQL transport at the budget transaction boundary."""
from __future__ import annotations


class UnsupportedBudgetBackendError(RuntimeError):
    """The selected database backend cannot support atomic budget accounting."""


def require_atomic_budget_backend(connection) -> None:
    from app.db.neon_http import NeonHttpConnection

    if isinstance(connection, NeonHttpConnection):
        raise UnsupportedBudgetBackendError(
            "Mission budget ledger requires native PostgreSQL transactions or the serialized SQLite writer; "
            "Neon HTTP SQL uses stateless requests and is unsupported"
        )
