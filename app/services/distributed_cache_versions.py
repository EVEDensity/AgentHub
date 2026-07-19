from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any


logger = logging.getLogger("agenthub.cache.version_bus")


class DistributedCacheVersionBus:
    CHANNEL = "agenthub:cache:versions"
    HASH_KEY = "agenthub:cache:version-counters"

    def __init__(self) -> None:
        self._redis: Any = None
        self._pubsub: Any = None
        self._listener: asyncio.Task | None = None
        self._instance_id = uuid.uuid4().hex[:12]

    async def start(self) -> bool:
        if os.getenv("AGENTHUB_DISTRIBUTED_CACHE_ENABLED", "true").lower() not in {"1", "true", "yes"}:
            return False
        try:
            import redis.asyncio as redis

            redis_url = os.getenv("REDIS_URL", "").strip()
            if not redis_url:
                redis_url = f"redis://{os.getenv('REDIS_ADDR', '127.0.0.1:6379')}/0"
            self._redis = redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await asyncio.wait_for(self._redis.ping(), timeout=3)
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(self.CHANNEL)
            self._listener = asyncio.create_task(self._listen(), name="cache-version-listener")
            logger.info("distributed cache version bus connected")
            return True
        except Exception as exc:
            logger.warning("distributed cache version bus disabled: %s", exc)
            await self.close()
            return False

    def schedule(self, kind: str, owner_id: str) -> None:
        if self._redis is None:
            return
        try:
            asyncio.get_running_loop().create_task(self.publish(kind, owner_id))
        except RuntimeError:
            return

    async def publish(self, kind: str, owner_id: str) -> int:
        if self._redis is None:
            return 0
        key = f"{kind}:{owner_id}"
        version = int(await self._redis.hincrby(self.HASH_KEY, key, 1))
        await self._redis.publish(
            self.CHANNEL,
            json.dumps({
                "kind": kind,
                "owner_id": owner_id,
                "version": version,
                "instance_id": self._instance_id,
            }),
        )
        return version

    async def close(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            try:
                await self._listener
            except asyncio.CancelledError:
                pass
            self._listener = None
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def _listen(self) -> None:
        assert self._pubsub is not None
        async for message in self._pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                payload = json.loads(message.get("data") or "{}")
                if payload.get("instance_id") == self._instance_id:
                    continue
                kind = str(payload["kind"])
                owner_id = str(payload["owner_id"])
                from app.services.context_summary_cache import context_summary_cache

                context_summary_cache.invalidate(kind, owner_id, propagate=False)
            except Exception:
                logger.warning("invalid cache version event", exc_info=True)


distributed_cache_version_bus = DistributedCacheVersionBus()
