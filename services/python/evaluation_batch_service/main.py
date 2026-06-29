"""Evaluation Batch Service — regression evaluation and quality scoring.

Subscribes to NATS JetStream for audit.security.events. Each audit event is
buffered for batch evaluation. The service exposes endpoints to:
  - GET /evaluation/recent: list recently evaluated events
  - POST /evaluation/run: trigger a batch evaluation on buffered events

In the initial landing, evaluation is a deterministic quality score based on
event metadata. P2 will add regression replay and load test analysis.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from prometheus_client import Counter, make_asgi_app

from shared.events import EventEnvelope
from shared.nats_client import NatsClient

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

EVAL_COUNT = Counter(
    "evaluation_events_total",
    "Total events evaluated",
    ["status"],
)

recent_events: list[dict[str, Any]] = []
MAX_RECENT = 100

nats_client: NatsClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global nats_client
    nats_url = os.getenv("NATS_URL", "nats://127.0.0.1:4222")
    nats_client = NatsClient(nats_url)
    await nats_client.connect()

    async def on_audit_event(envelope: EventEnvelope):
        logger.info(
            "received audit event for session %s (type=%s)",
            envelope.session_id,
            envelope.event_type,
        )
        try:
            score = evaluate_event(envelope)
            recent_events.append({
                "event_id": envelope.event_id,
                "session_id": envelope.session_id,
                "tenant_id": envelope.tenant_id,
                "event_type": str(envelope.event_type),
                "score": score,
                "evaluated_at": time.time(),
            })
            if len(recent_events) > MAX_RECENT:
                recent_events.pop(0)
            EVAL_COUNT.labels(status="ok").inc()
        except Exception as e:
            logger.error("evaluation failed: %s", e)
            EVAL_COUNT.labels(status="error").inc()

    await nats_client.subscribe(
        "evaluation-audit",
        "agenthub.audit.security.events",
        on_audit_event,
    )

    # Also subscribe to agent runtime results for quality tracking
    async def on_runtime_result(envelope: EventEnvelope):
        status = envelope.payload.get("status", "unknown")
        logger.info(
            "runtime result for session %s: status=%s pool=%s",
            envelope.session_id,
            status,
            envelope.payload.get("pool"),
        )
        score = 1.0 if status == "completed" else 0.0
        recent_events.append({
            "event_id": envelope.event_id,
            "session_id": envelope.session_id,
            "tenant_id": envelope.tenant_id,
            "event_type": str(envelope.event_type),
            "score": score,
            "pool": envelope.payload.get("pool"),
            "evaluated_at": time.time(),
        })
        if len(recent_events) > MAX_RECENT:
            recent_events.pop(0)
        EVAL_COUNT.labels(status=status).inc()

    await nats_client.subscribe(
        "evaluation-runtime-results",
        "agenthub.agent.runtime.results",
        on_runtime_result,
    )

    logger.info("evaluation-batch-service started, subscribed to NATS")
    yield

    if nats_client:
        await nats_client.close()


app = FastAPI(title="evaluation-batch-service", version="0.1.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


def evaluate_event(envelope: EventEnvelope) -> float:
    """Deterministic quality score for an event. Returns a float in [0, 1]."""
    # Simple heuristic: events with more payload data score higher
    payload_size = len(str(envelope.payload))
    score = min(payload_size / 1000.0, 1.0)
    return round(score, 3)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    connected = nats_client.connected if nats_client else False
    status = "ok" if connected else "degraded"
    return {"status": status, "service": "evaluation-batch-service", "nats_connected": str(connected)}


@app.get("/profile")
async def profile() -> dict:
    return {
        "service": "evaluation-batch-service",
        "responsibilities": [
            "regression evaluation",
            "quality scoring",
            "load test replay analysis",
            "NATS event subscription (audit.security, agent.runtime.results)",
        ],
        "subscriptions": [
            {"subject": "agenthub.audit.security.events", "durable": "evaluation-audit"},
            {"subject": "agenthub.agent.runtime.results", "durable": "evaluation-runtime-results"},
        ],
    }


@app.get("/evaluation/recent")
async def recent() -> dict:
    return {"count": len(recent_events), "events": recent_events}
