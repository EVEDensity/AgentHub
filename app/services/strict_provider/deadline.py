"""Local dispatch fence; it cannot retract a request already sent to a peer."""

import asyncio


def ensure_active(deadline: float) -> None:
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
    if asyncio.get_running_loop().time() >= deadline:
        raise TimeoutError("strict provider operation deadline expired")
