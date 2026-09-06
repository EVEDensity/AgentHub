from __future__ import annotations

import asyncio

from app.services.mission_event_bus import MissionEventBus


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
