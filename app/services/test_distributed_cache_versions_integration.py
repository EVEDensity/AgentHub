from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.services.context_summary_cache import context_summary_cache
from app.services.distributed_cache_versions import DistributedCacheVersionBus


def test_redis_version_event_invalidates_peer_cache() -> None:
    if os.getenv("AGENTHUB_RUN_REDIS_INTEGRATION") != "1":
        pytest.skip("set AGENTHUB_RUN_REDIS_INTEGRATION=1 with Redis running")

    async def run() -> None:
        listener = DistributedCacheVersionBus()
        publisher = DistributedCacheVersionBus()
        assert await listener.start()
        assert await publisher.start()
        owner_id = f"integration-{uuid.uuid4().hex[:12]}"
        context_summary_cache.set("route", owner_id, "active-routes", "cached")
        assert context_summary_cache.get("route", owner_id, "active-routes") == "cached"

        version = await publisher.publish("route", owner_id)
        assert version > 0
        for _ in range(30):
            await asyncio.sleep(0.1)
            if context_summary_cache.get("route", owner_id, "active-routes") is None:
                break
        assert context_summary_cache.get("route", owner_id, "active-routes") is None
        await publisher.close()
        await listener.close()

    asyncio.run(run())
