from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import UTC, datetime

import pytest


def test_nats_rust_summary_online_contract() -> None:
    if os.getenv("AGENTHUB_RUN_NATS_INTEGRATION") != "1":
        pytest.skip("set AGENTHUB_RUN_NATS_INTEGRATION=1 with NATS/Rust/summarization services running")

    async def run() -> None:
        import nats

        nc = await nats.connect(os.getenv("NATS_URL", "nats://127.0.0.1:4222"))
        session_id = f"integration-{uuid.uuid4().hex[:10]}"
        event_id = f"compact-{uuid.uuid4().hex}"
        summary_future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()

        async def on_summary(message) -> None:
            envelope = json.loads(message.data)
            if envelope.get("session_id") == session_id and not summary_future.done():
                summary_future.set_result(envelope)

        subscription = await nc.subscribe("agenthub.session.summary", cb=on_summary)
        messages = [
            {
                "sequence": index,
                "role": "user" if index % 2 else "assistant",
                "content": f"integration memory message {index}",
                "token_count": 8,
                "timestamp": int(time.time()),
            }
            for index in range(1, 41)
        ]
        request = {
            "event_id": event_id,
            "event_type": "memory.compact.requested",
            "event_version": 1,
            "occurred_at": datetime.now(UTC).isoformat(),
            "trace_id": uuid.uuid4().hex,
            "tenant_id": "integration-user",
            "session_id": session_id,
            "message_id": None,
            "actor_id": "integration-test",
            "producer": {"service": "integration-test", "instance": "pytest", "region": None},
            "routing": {"channel": "memory", "partition_key": session_id, "priority": "normal"},
            "payload": {"messages": messages},
        }
        await nc.publish("agenthub.memory.compact.requested", json.dumps(request).encode("utf-8"))
        await nc.flush()

        envelope = await asyncio.wait_for(summary_future, timeout=45)
        payload = envelope["payload"]
        assert envelope["event_type"] == "session.summary.generated"
        assert payload["source"] == "memory-compact"
        assert payload["tokens_before"] > 0
        assert payload["tokens_after"] > 0
        assert payload["covered_sequence_start"] == 1
        assert payload["covered_sequence_end"] == 30
        assert payload["summary"]

        await subscription.unsubscribe()
        await nc.drain()

    asyncio.run(run())
