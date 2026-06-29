"""Shared NATS JetStream client for Python offline services.

This module provides a thin async wrapper around nats-py that handles:
  - Connection with automatic reconnection
  - JetStream stream/consumer creation (idempotent)
  - Durable subscription with auto-ack
  - Event envelope (de)serialization via shared.events.EventEnvelope

Usage:

    from shared.nats_client import NatsClient, EventType

    client = NatsClient("nats://127.0.0.1:4222")
    await client.connect()

    async def handler(envelope: EventEnvelope):
        print(f"received {envelope.event_type}: {envelope.payload}")

    await client.subscribe("my-service", "agenthub.session.stream.events", handler)

    # On shutdown:
    await client.close()
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from .events import EventEnvelope

logger = logging.getLogger(__name__)

# Stream definitions matching the Go eventbus package.
STREAM_DEFS = [
    {"name": "SESSION", "subjects": ["agenthub.session.>"], "max_age_seconds": 259200},  # 72h
    {"name": "AGENT-RUNTIME", "subjects": ["agenthub.agent.runtime.>"], "max_age_seconds": 86400},  # 24h
    {"name": "RETRIEVAL", "subjects": ["agenthub.retrieval.>"], "max_age_seconds": 86400},
    {"name": "KNOWLEDGE", "subjects": ["agenthub.knowledge.>"], "max_age_seconds": 604800},  # 7d
    {"name": "TOOL-PERMISSIONS", "subjects": ["agenthub.tool.permission.>"], "max_age_seconds": 604800},  # 7d
    {"name": "AUDIT", "subjects": ["agenthub.audit.>"], "max_age_seconds": 7776000},  # 90d
]

Handler = Callable[[EventEnvelope], Awaitable[None]]


class NatsClient:
    """Async NATS JetStream client for Python offline services."""

    def __init__(self, url: str = "nats://127.0.0.1:4222") -> None:
        self._url = url
        self._nc = None
        self._js = None
        self._subs: list[Any] = []

    async def connect(self) -> None:
        """Connect to NATS and ensure all platform streams exist."""
        try:
            import nats
        except ImportError:
            logger.warning("nats-py not installed; NATS subscription disabled")
            return

        self._nc = await nats.connect(
            self._url,
            name="agenthub-python",
            max_reconnect_attempts=-1,
            reconnect_time_wait=2,
        )
        self._js = self._nc.jetstream()

        for stream_def in STREAM_DEFS:
            try:
                await self._js.stream_info(stream_def["name"])
            except Exception:
                try:
                    from nats.js.api import StreamConfig, StorageType, RetentionPolicy

                    config = StreamConfig(
                        name=stream_def["name"],
                        subjects=stream_def["subjects"],
                        retention=RetentionPolicy.LIMITS,
                        max_age=stream_def["max_age_seconds"],
                        storage=StorageType.FILE,
                    )
                    await self._js.add_stream(config)
                    logger.info("created jetstream stream %s", stream_def["name"])
                except Exception as e:
                    logger.warning("could not create stream %s: %s", stream_def["name"], e)

        logger.info("connected to NATS at %s", self._url)

    @property
    def connected(self) -> bool:
        return self._nc is not None and not self._nc.is_closed

    async def subscribe(self, durable: str, subject: str, handler: Handler) -> None:
        """Subscribe to a subject with a durable consumer. The handler is called
        for each event; successful return auto-acks, exceptions nak the message."""
        if self._js is None:
            logger.warning("NATS not connected; skipping subscription to %s", subject)
            return

        async def _wrapped(msg):
            try:
                data = json.loads(msg.data)
                envelope = EventEnvelope(**data)
                await handler(envelope)
                await msg.ack()
            except Exception as e:
                logger.error("handler error for %s: %s", subject, e)
                await msg.nak()

        from nats.js.api import ConsumerConfig, DeliverPolicy

        sub = await self._js.subscribe(
            subject,
            durable=durable,
            cb=_wrapped,
            deliver_policy=DeliverPolicy.ALL,
            config=ConsumerConfig(
                ack_wait=30,
                max_deliver=5,
            ),
        )
        self._subs.append(sub)
        logger.info("subscribed to %s (durable=%s)", subject, durable)

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        """Publish an event envelope to a subject via JetStream."""
        if self._js is None:
            logger.warning("NATS not connected; cannot publish to %s", subject)
            return
        data = envelope.model_dump_json().encode()
        await self._js.publish(subject, data)

    async def close(self) -> None:
        """Drain and close all subscriptions and the connection."""
        for sub in self._subs:
            try:
                await sub.unsubscribe()
            except Exception:
                pass
        self._subs.clear()
        if self._nc and not self._nc.is_closed:
            await self._nc.drain()
            await self._nc.close()
        logger.info("NATS client closed")
