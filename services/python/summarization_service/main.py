"""Summarization Service — offline session consolidation and report generation.

Subscribes to NATS JetStream for session.stream.complete events. When a session
stream completes, this service:
  1. Collects all stream chunks for the session (via Redis replay)
  2. Generates a concise summary using the model-adapter-service
  3. Publishes the summary back as a session.summary event
  4. Optionally writes a memory checkpoint to PostgreSQL

In the initial landing, the summary is a deterministic concatenation of chunk
contents. P2 will replace this with a real LLM call via model-adapter.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from prometheus_client import Counter, make_asgi_app

from shared.events import EventEnvelope, EventType
from shared.nats_client import NatsClient

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Prometheus metrics
SUMMARY_COUNT = Counter(
    "summarization_sessions_total",
    "Total sessions summarized",
    ["status"],
)

# In-memory buffer of recent summaries for debugging
recent_summaries: list[dict[str, Any]] = []
MAX_RECENT = 50

# NATS client (initialized on startup)
nats_client: NatsClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect to NATS and subscribe. Shutdown: drain and close."""
    global nats_client
    nats_url = os.getenv("NATS_URL", "nats://127.0.0.1:4222")
    nats_client = NatsClient(nats_url)
    await nats_client.connect()

    # Subscribe to session stream complete events (queue group for competing consumers)
    async def on_stream_complete(envelope: EventEnvelope):
        logger.info(
            "received stream.complete for session %s (trace=%s)",
            envelope.session_id,
            envelope.trace_id,
        )
        try:
            await generate_summary(envelope)
            SUMMARY_COUNT.labels(status="ok").inc()
        except Exception as e:
            logger.error("summary generation failed: %s", e)
            SUMMARY_COUNT.labels(status="error").inc()

    await nats_client.subscribe(
        "summarization-stream-complete",
        "agenthub.session.stream.events",
        on_stream_complete,
    )

    # Also subscribe to react transition events for finished states
    async def on_react_transition(envelope: EventEnvelope):
        to_state = envelope.payload.get("to_state", "")
        if to_state == "finished":
            logger.info(
                "react loop finished for session %s (steps=%s)",
                envelope.session_id,
                envelope.payload.get("step_count"),
            )
            try:
                await generate_summary(envelope)
                SUMMARY_COUNT.labels(status="ok").inc()
            except Exception as e:
                logger.error("summary generation failed: %s", e)
                SUMMARY_COUNT.labels(status="error").inc()

    await nats_client.subscribe(
        "summarization-react-finished",
        "agenthub.session.stream.events",
        on_react_transition,
    )

    logger.info("summarization-service started, subscribed to NATS")
    yield

    # Shutdown
    if nats_client:
        await nats_client.close()


app = FastAPI(title="summarization-service", version="0.1.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


async def generate_summary(envelope: EventEnvelope) -> dict[str, Any]:
    """Generate a summary for a completed session.

    In the initial landing this is a deterministic placeholder. P2 will call
    the model-adapter-service for a real LLM-generated summary.
    """
    session_id = envelope.session_id
    tenant_id = envelope.tenant_id
    trace_id = envelope.trace_id

    summary_text = (
        f"Session {session_id} completed. "
        f"Trace: {trace_id}. "
        f"Event type: {envelope.event_type}. "
        f"Final state: {envelope.payload.get('to_state', 'unknown')}. "
        f"Steps: {envelope.payload.get('step_count', 'N/A')}."
    )

    summary = {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "trace_id": trace_id,
        "summary": summary_text,
        "generated_at": time.time(),
        "event_source": envelope.event_id,
    }

    # Buffer for debugging
    recent_summaries.append(summary)
    if len(recent_summaries) > MAX_RECENT:
        recent_summaries.pop(0)

    # Publish summary event back to NATS (best-effort)
    if nats_client and nats_client.connected:
        summary_envelope = EventEnvelope(
            event_id=f"summary-{envelope.event_id}",
            event_type="session.summary.generated",
            trace_id=trace_id,
            tenant_id=tenant_id,
            session_id=session_id,
            producer={"service": "summarization-service", "instance": os.getenv("HOSTNAME", "local")},
            payload=summary,
        )
        await nats_client.publish("agenthub.session.summary", summary_envelope)
        logger.info("published summary for session %s", session_id)

    return summary


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    connected = nats_client.connected if nats_client else False
    status = "ok" if connected else "degraded"
    return {"status": status, "service": "summarization-service", "nats_connected": str(connected)}


@app.get("/profile")
async def profile() -> dict:
    return {
        "service": "summarization-service",
        "responsibilities": [
            "offline consolidation",
            "session summarization",
            "report generation",
            "memory checkpoint writing",
            "NATS event subscription (session.stream.complete, agent.react.transition)",
        ],
        "subscriptions": [
            {"subject": "agenthub.session.stream.events", "durable": "summarization-stream-complete"},
            {"subject": "agenthub.session.stream.events", "durable": "summarization-react-finished"},
        ],
    }


@app.get("/summaries/recent")
async def recent() -> dict:
    return {"count": len(recent_summaries), "summaries": recent_summaries}
