from __future__ import annotations

import asyncio

from app.services.mission_event_bus import MissionEventBus, PostgresMissionEventNotifier


def test_notifications_are_coalesced_and_subscriptions_are_removed() -> None:
    async def scenario() -> None:
        bus = MissionEventBus()
        async with bus.subscribe("mis-1") as queue:
            await bus.notify("mis-1")
            await bus.notify("mis-1")
            assert queue.qsize() == 1
            await asyncio.wait_for(queue.get(), timeout=0.1)
        # A notification after unsubscribe is a no-op and must not raise.
        await bus.notify("mis-1")

    asyncio.run(scenario())


def test_notifications_are_scoped_by_mission() -> None:
    async def scenario() -> None:
        bus = MissionEventBus()
        async with bus.subscribe("mis-1") as first, bus.subscribe("mis-2") as second:
            await bus.notify("mis-1")
            assert first.qsize() == 1
            assert second.qsize() == 0

    asyncio.run(scenario())


def test_postgres_listener_forwards_and_reconnects_after_termination() -> None:
    class Connection:
        def __init__(self) -> None:
            self.listener = None
            self.termination_listener = None
            self.closed = False

        async def add_listener(self, channel, callback) -> None:
            assert channel == "agenthub_mission_events"
            self.listener = callback

        def add_termination_listener(self, callback) -> None:
            self.termination_listener = callback

        async def close(self) -> None:
            self.closed = True

    async def scenario() -> None:
        bus = MissionEventBus()
        connections: list[Connection] = []
        reconnected = asyncio.Event()

        async def connect(database_url: str) -> Connection:
            assert database_url == "postgresql://events"
            connection = Connection()
            connections.append(connection)
            if len(connections) == 2:
                reconnected.set()
            return connection

        notifier = PostgresMissionEventNotifier(
            "postgresql://events",
            bus,
            connect=connect,
            reconnect_min_seconds=0.001,
            reconnect_max_seconds=0.01,
        )
        await notifier.start()
        async with bus.subscribe("mis-1") as queue:
            assert connections[0].listener is not None
            connections[0].listener(None, 1, notifier.channel, "mis-1")
            await asyncio.wait_for(queue.get(), timeout=0.1)
        assert connections[0].termination_listener is not None
        connections[0].termination_listener(connections[0])
        await asyncio.wait_for(reconnected.wait(), timeout=0.2)
        await notifier.stop()
        await notifier.stop()
        assert all(connection.closed for connection in connections)

    asyncio.run(scenario())


def test_postgres_listener_rejects_unbounded_retry_configuration() -> None:
    bus = MissionEventBus()
    try:
        PostgresMissionEventNotifier(
            "postgresql://events",
            bus,
            reconnect_min_seconds=2,
            reconnect_max_seconds=1,
        )
    except ValueError as exc:
        assert "reconnect" in str(exc)
    else:
        raise AssertionError("invalid reconnect configuration was accepted")
