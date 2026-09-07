"""Process lifecycle helpers for CLI cancellation and cleanup."""

from __future__ import annotations

import signal
from contextlib import contextmanager
from threading import Event
from typing import Callable, Iterator


@contextmanager
def cancellation_scope(
    cancel_event: Event,
    *,
    on_signal: Callable[[], None] | None = None,
) -> Iterator[Event]:
    """Install a temporary SIGINT handler that requests cooperative cancel.

    Python only permits signal registration on the main thread. Worker and
    test callers therefore degrade to the existing handler without raising a
    secondary error. The previous handler is always restored.
    """
    previous = None
    installed = False

    def _handle(signum: int, frame: object) -> None:  # pragma: no cover - OS callback
        cancel_event.set()
        if on_signal is not None:
            on_signal()

    try:
        previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _handle)
        installed = True
    except (ValueError, OSError):
        # Called outside the main thread or on a platform without SIGINT.
        installed = False
    try:
        yield cancel_event
    finally:
        if installed and previous is not None:
            signal.signal(signal.SIGINT, previous)


__all__ = ["cancellation_scope"]
