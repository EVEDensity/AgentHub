from threading import Event
from unittest.mock import patch

from app.cli.lifecycle import cancellation_scope


def test_cancellation_scope_sets_event_and_restores_handler():
    event = Event()
    callbacks = []
    with patch("app.cli.lifecycle.signal.signal") as install:
        with cancellation_scope(event, on_signal=lambda: callbacks.append("signal")):
            handler = install.call_args.args[1]
            handler(2, None)
            assert event.is_set()
            assert callbacks == ["signal"]
        assert install.call_count == 2
