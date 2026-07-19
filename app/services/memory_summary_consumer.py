from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

from app.config import MEMORY_DIR
from app.services.memory.session_memory import SessionMemoryManager
from app.services.memory.storage import MemoryStorage
from app.services.performance_monitor import monitor
from app.services.token_budget import count_tokens


logger = logging.getLogger("agenthub.memory.summary_consumer")


class MemorySummaryConsumer:
    """Persist semantic summaries emitted by the offline memory pipeline."""

    def __init__(self) -> None:
        self._nc: Any = None
        self._subscription: Any = None
        self._seen_order: deque[str] = deque(maxlen=2048)
        self._seen: set[str] = set()

    async def start(self) -> bool:
        if os.getenv("AGENTHUB_MEMORY_EVENTS_ENABLED", "true").lower() not in {"1", "true", "yes"}:
            return False
        try:
            import nats

            self._nc = await asyncio.wait_for(
                nats.connect(
                    os.getenv("NATS_URL", "nats://127.0.0.1:4222"),
                    name="agenthub-online-memory-consumer",
                    connect_timeout=2,
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=2,
                ),
                timeout=3,
            )
            js = self._nc.jetstream()
            try:
                self._subscription = await js.subscribe(
                    "agenthub.session.summary",
                    durable="agenthub-online-memory-summary-v1",
                    cb=self._handle_message,
                    manual_ack=True,
                )
            except Exception:
                # Startup ordering must not disable future summary delivery when
                # the SESSION JetStream is created by another service later.
                self._subscription = await self._nc.subscribe(
                    "agenthub.session.summary", cb=self._handle_core_message,
                )
                logger.warning("SESSION stream unavailable; using live NATS summary subscription")
            logger.info("memory summary consumer subscribed")
            return True
        except Exception as exc:
            logger.warning("memory summary consumer disabled: %s", exc)
            await self.close()
            return False

    async def close(self) -> None:
        if self._subscription is not None:
            try:
                await self._subscription.unsubscribe()
            except Exception:
                pass
            self._subscription = None
        if self._nc is not None and not self._nc.is_closed:
            try:
                await self._nc.drain()
            except Exception:
                await self._nc.close()
        self._nc = None

    async def _handle_message(self, message: Any) -> None:
        try:
            envelope = json.loads(message.data)
            await self.consume_envelope(envelope)
            await message.ack()
        except Exception:
            logger.exception("failed to consume session summary event")
            await message.nak()

    async def _handle_core_message(self, message: Any) -> None:
        try:
            await self.consume_envelope(json.loads(message.data))
        except Exception:
            logger.exception("failed to consume live session summary event")

    async def consume_envelope(self, envelope: dict[str, Any]) -> bool:
        if envelope.get("event_type") != "session.summary.generated":
            return False
        event_id = str(envelope.get("event_id", ""))
        if event_id and event_id in self._seen:
            return False

        payload = envelope.get("payload") or {}
        session_id = str(payload.get("session_id") or envelope.get("session_id") or "").strip()
        summary = str(payload.get("summary") or "").strip()
        tenant_id = str(payload.get("tenant_id") or envelope.get("tenant_id") or "local-admin")
        user_id = re.sub(r"[^A-Za-z0-9_.@-]", "_", tenant_id)[:128] or "local-admin"
        if not session_id or not summary:
            return False

        manager = SessionMemoryManager(MemoryStorage(MEMORY_DIR / "users" / user_id))
        await manager.write_session_summary(session_id, summary)
        try:
            from app.services.agent_service import _invalidate_memory_cache

            _invalidate_memory_cache()
        except Exception:
            logger.debug("memory cache invalidation unavailable", exc_info=True)

        monitor.record_summary_usage(
            int(payload.get("summary_tokens") or 0),
            estimated_cost=float(payload.get("estimated_cost") or 0.0),
            quality_score=(
                float(payload["quality_score"])
                if payload.get("quality_score") is not None else None
            ),
        )
        if event_id:
            if len(self._seen_order) == self._seen_order.maxlen:
                oldest = self._seen_order.popleft()
                self._seen.discard(oldest)
            self._seen_order.append(event_id)
            self._seen.add(event_id)
        return True

    async def request_compaction(self, session_id: str, user_id: str) -> bool:
        """Publish a bounded conversation window for Rust compaction."""
        if self._nc is None or self._nc.is_closed:
            return False
        from app.db.session import afetch_all

        rows = await afetch_all(
            "SELECT id,sender,content,created_at FROM messages "
            "WHERE session_id=$1 AND type!='system' ORDER BY created_at DESC LIMIT 60",
            session_id,
        )
        rows.reverse()
        if len(rows) < 20:
            return False
        messages = []
        for index, row in enumerate(rows, start=1):
            sender = str(row.get("sender") or "user").lower()
            role = "user" if sender in {"user", user_id.lower()} else "assistant"
            content = str(row.get("content") or "")
            messages.append({
                "sequence": index,
                "role": role,
                "content": content,
                "token_count": count_tokens(content),
                "timestamp": None,
            })
        event_id = f"compact-{session_id}-{rows[-1].get('id') or uuid.uuid4().hex[:8]}"
        envelope = {
            "event_id": event_id,
            "event_type": "memory.compact.requested",
            "event_version": 1,
            "occurred_at": datetime.now(UTC).isoformat(),
            "trace_id": uuid.uuid4().hex,
            "tenant_id": user_id or "local-admin",
            "session_id": session_id,
            "message_id": str(rows[-1].get("id") or "") or None,
            "actor_id": user_id or None,
            "producer": {"service": "agenthub-online", "instance": "local", "region": None},
            "routing": {"channel": "memory", "partition_key": session_id, "priority": "normal"},
            "payload": {"messages": messages},
        }
        await self._nc.publish(
            "agenthub.memory.compact.requested",
            json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
        )
        return True


memory_summary_consumer = MemorySummaryConsumer()
