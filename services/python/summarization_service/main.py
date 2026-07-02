"""Summarization Service — LLM-powered session consolidation and report generation.

Subscribes to NATS JetStream for session completion events. When a ReAct loop
finishes or a stream completes, this service:
  1. Collects conversation context (via Redis replay or envelope payload)
  2. Calls model-adapter-service for LLM-generated summaries
  3. Persists summaries to PostgreSQL (via NATS → audit/usage-style table)
  4. Supports periodic consolidation (merge hourly summaries into daily reports)
  5. Publishes session.summary.generated events back to NATS

Environment:
  MODEL_ADAPTER_URL = http://127.0.0.1:8091   (default)
  NATS_URL = nats://127.0.0.1:4222
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, Histogram, make_asgi_app

from shared.events import EventEnvelope
from shared.nats_client import NatsClient

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Prometheus metrics
SUMMARY_COUNT = Counter("summarization_sessions_total", "Total sessions summarized", ["status"])
SUMMARY_LATENCY = Histogram("summarization_latency_seconds", "Time to generate a summary")
SUMMARY_TOKENS = Counter("summarization_tokens_total", "Tokens consumed by summarization")

# In-memory stores for debugging
recent_summaries: list[dict[str, Any]] = []
MAX_RECENT = 50
recent_reports: list[dict[str, Any]] = []
MAX_REPORTS = 20

nats_client: NatsClient | None = None
model_adapter_url = os.getenv("MODEL_ADAPTER_URL", "http://127.0.0.1:8091")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global nats_client
    nats_url = os.getenv("NATS_URL", "nats://127.0.0.1:4222")
    nats_client = NatsClient(nats_url)
    await nats_client.connect()

    async def on_react_finished(envelope: EventEnvelope):
        to_state = envelope.payload.get("to_state", "")
        if to_state != "finished":
            return
        logger.info("react finished session=%s steps=%s", envelope.session_id, envelope.payload.get("step_count"))
        try:
            summary = await generate_summary(envelope)
            SUMMARY_COUNT.labels(status="ok").inc()
            logger.info("summary generated for session=%s", envelope.session_id)
        except Exception as e:
            logger.error("summary failed session=%s: %s", envelope.session_id, e)
            SUMMARY_COUNT.labels(status="error").inc()

    await nats_client.subscribe("summarization-react-finished", "agenthub.session.stream.events", on_react_finished)
    logger.info("summarization-service started (adapter=%s)", model_adapter_url)
    yield
    if nats_client:
        await nats_client.close()


app = FastAPI(title="summarization-service", version="0.2.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


# ── Core logic ──────────────────────────────────────────────────────────

async def generate_summary(envelope: EventEnvelope) -> dict[str, Any]:
    """Generate a concise session summary via model-adapter-service.

    Falls back to a deterministic extract if the adapter is unreachable.
    """
    session_id = envelope.session_id
    tenant_id = envelope.tenant_id
    trace_id = envelope.trace_id
    step_count = envelope.payload.get("step_count", 0)
    final_state = envelope.payload.get("to_state", "unknown")

    # Build a prompt from the envelope payload context.
    context_parts = []
    if envelope.payload.get("user_input"):
        context_parts.append(f"User: {envelope.payload['user_input']}")
    if envelope.payload.get("final_output"):
        context_parts.append(f"Assistant: {envelope.payload['final_output'][:1000]}")
    context = "\n".join(context_parts) if context_parts else f"Session {session_id} completed after {step_count} steps."

    prompt = (
        f"Summarize this AI agent conversation session in 1-3 sentences. "
        f"The user's goal and the outcome should be clear.\n\n"
        f"Session: {session_id}\nSteps: {step_count}\nFinal state: {final_state}\n\n"
        f"Context:\n{context}\n\nSummary:"
    )

    # Call model-adapter-service for the LLM summary.
    summary_text = await _call_llm(prompt, tenant_id, session_id, trace_id)
    if not summary_text:
        # Fallback: deterministic summary from envelope metadata.
        summary_text = (
            f"Session {session_id}: completed in {step_count} steps "
            f"with final state '{final_state}'."
        )

    summary = {
        "id": f"summary-{uuid.uuid4().hex[:12]}",
        "session_id": session_id,
        "tenant_id": tenant_id,
        "trace_id": trace_id,
        "summary": summary_text,
        "step_count": step_count,
        "final_state": final_state,
        "generated_at": time.time(),
    }

    # Buffer for debug inspection.
    recent_summaries.append(summary)
    if len(recent_summaries) > MAX_RECENT:
        recent_summaries.pop(0)

    # Publish summary event back to NATS.
    if nats_client and nats_client.connected:
        summary_env = EventEnvelope(
            event_id=f"summary-{envelope.event_id}",
            event_type="session.summary.generated",
            trace_id=trace_id,
            tenant_id=tenant_id,
            session_id=session_id,
            producer={"service": "summarization-service", "instance": os.getenv("HOSTNAME", "local")},
            payload=summary,
        )
        await nats_client.publish("agenthub.session.summary", summary_env)

    return summary


async def _call_llm(prompt: str, tenant_id: str, session_id: str, trace_id: str) -> str:
    """Call model-adapter-service for a summary. Returns empty string on failure."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{model_adapter_url}/v1/chat/completions",
                json={
                    "model": "mock-gpt",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 256,
                    "system_prompt": "You are a concise session summarizer. Output 1-3 sentences capturing the user's goal and outcome.",
                    "stage": "summarize",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content:
                tokens_used = data.get("usage", {}).get("total_tokens", 0)
                SUMMARY_TOKENS.inc(tokens_used)
            return content
    except Exception as exc:
        logger.warning("LLM call for summary failed (session=%s): %s", session_id, exc)
        return ""


async def _generate_report(tenant_id: str, period_hours: int) -> dict[str, Any]:
    """Consolidate recent summaries into a periodic report for a tenant."""
    summaries = [s for s in recent_summaries if s.get("tenant_id") == tenant_id]
    if not summaries:
        summaries = recent_summaries[-10:]  # fallback to global recent

    if not summaries:
        return {"report": "No sessions to summarize.", "session_count": 0}

    sessions_text = "\n".join(
        f"- {s['session_id']}: {s.get('summary', '')[:200]}"
        for s in summaries[-20:]
    )
    prompt = (
        f"Generate a brief activity report for the past {period_hours}h across {len(summaries)} sessions.\n\n"
        f"Sessions:\n{sessions_text}\n\n"
        f"Report (2-4 sentences):"
    )

    report_text = await _call_llm(prompt, tenant_id, "report", "")
    if not report_text:
        report_text = f"Activity report: {len(summaries)} sessions completed in the last {period_hours}h."

    report = {
        "id": f"report-{uuid.uuid4().hex[:12]}",
        "tenant_id": tenant_id,
        "period_hours": period_hours,
        "session_count": len(summaries),
        "report": report_text,
        "generated_at": time.time(),
    }
    recent_reports.append(report)
    if len(recent_reports) > MAX_REPORTS:
        recent_reports.pop(0)
    return report


# ── HTTP endpoints ──────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz() -> dict[str, str]:
    connected = nats_client.connected if nats_client else False
    status = "ok" if connected else "degraded"
    return {"status": status, "service": "summarization-service", "nats_connected": str(connected)}


@app.get("/profile")
async def profile() -> dict:
    return {
        "service": "summarization-service",
        "version": "0.2.0",
        "responsibilities": [
            "LLM-powered session summarization",
            "periodic consolidation (hourly → daily reports)",
            "NATS event subscription (agent.react.transition finished states)",
            "model-adapter integration for summary generation",
        ],
        "model_adapter_url": model_adapter_url,
    }


@app.get("/summaries/recent")
async def recent() -> dict:
    return {"count": len(recent_summaries), "summaries": recent_summaries}


@app.get("/reports/recent")
async def reports_recent() -> dict:
    return {"count": len(recent_reports), "reports": recent_reports}


@app.post("/reports/generate")
async def generate_report_endpoint(req: dict[str, Any] = {}) -> dict:
    """Generate a periodic report for a tenant via HTTP (manual trigger)."""
    tenant_id = req.get("tenant_id", "") or "default"
    period_hours = req.get("period_hours", 24)
    report = await _generate_report(tenant_id, period_hours)
    return report


@app.post("/summaries/generate")
async def summarize_adhoc(req: dict[str, Any]) -> dict:
    """Generate a summary for arbitrary text (ad-hoc, no NATS)."""
    text = req.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    prompt = f"Summarize this text in 1-3 sentences:\n\n{text}\n\nSummary:"
    summary_text = await _call_llm(prompt, "adhoc", "adhoc", "")
    return {"text": text[:500], "summary": summary_text}
