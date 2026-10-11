"""Keep private attempt exclusion through durable artifact reporting."""
from contextlib import asynccontextmanager


def close_recovery(harness):
    close = getattr(harness, "close_recovery", None)
    if callable(close):
        close()


@asynccontextmanager
async def runner_recovery_scope(harness):
    defer = getattr(harness, "defer_recovery_close", None)
    if callable(defer):
        defer()
    try:
        yield
    finally:
        close_recovery(harness)
