from __future__ import annotations

"""Performance monitoring for the model connection pipeline.

Tracks key metrics across the full request lifecycle:
  - LLM call latency (p50/p95/p99 via histogram bucketing)
  - Request success/failure rates per model
  - Streaming chunk throughput
  - WebSocket broadcast timing
  - Degradation events
  - HTTP retry counts

All metrics are in-process (no external TSDB dependency).  A lightweight
API endpoint exposes the latest snapshot for dashboard integration.
"""

import asyncio
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agenthub.perf")

# ═══════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class LatencyHistogram:
    """Bucketed latency histogram for percentile computation."""
    # Bucket edges in milliseconds
    buckets: list[float] = field(default_factory=lambda: [
        200, 400, 600, 800, 1000, 1500, 2000, 3000, 5000,
        8000, 12000, 20000, 30000, 60000, 120000,
    ])
    counts: list[int] = field(default_factory=lambda: [0] * 16)
    total: int = 0
    _sum: float = 0.0  # total latency in ms

    def record(self, latency_ms: float) -> None:
        self.total += 1
        self._sum += latency_ms
        for i, edge in enumerate(self.buckets):
            if latency_ms <= edge:
                self.counts[i] += 1
                return
        # Above largest bucket
        self.counts[-1] += 1

    def percentile(self, pct: float) -> float:
        """Return approximate p-th percentile latency in ms."""
        if self.total == 0:
            return 0.0
        target = int(self.total * pct / 100)
        cumulative = 0
        for i, count in enumerate(self.counts):
            cumulative += count
            if cumulative >= target:
                return self.buckets[i]
        return self.buckets[-1]

    @property
    def avg(self) -> float:
        return self._sum / max(1, self.total)


@dataclass
class ModelMetrics:
    """Per-model performance counters."""
    provider: str = ""
    model_name: str = ""
    total_calls: int = 0
    success: int = 0
    failures: int = 0
    retries: int = 0  # HTTP-level retries triggered
    latency: LatencyHistogram = field(default_factory=LatencyHistogram)
    last_latency_ms: float = 0.0
    last_error: str = ""
    last_error_at: float = 0.0


@dataclass
class StreamMetrics:
    """Streaming performance counters."""
    total_streams: int = 0
    total_chunks: int = 0
    total_bytes: int = 0
    # Time-to-first-token (ms)
    ttft_hist: LatencyHistogram = field(default_factory=LatencyHistogram)
    last_ttft_ms: float = 0.0
    # Inter-chunk latency (ms)
    chunk_gap_hist: LatencyHistogram = field(default_factory=LatencyHistogram)
    last_chunk_gap_ms: float = 0.0


@dataclass
class WsMetrics:
    """WebSocket broadcast performance counters."""
    total_broadcasts: int = 0
    total_sends: int = 0
    failures: int = 0
    timeouts: int = 0
    broadcast_latency: LatencyHistogram = field(default_factory=LatencyHistogram)


@dataclass
class DegradationEvent:
    """Record of a single degradation entry/exit."""
    session_id: str = ""
    entered_at: float = 0.0
    exited_at: float = 0.0
    reason: str = ""
    failed_models: list[str] = field(default_factory=list)
    recovery_attempts: int = 0


# ═══════════════════════════════════════════════════════════════════════
# Monitor singleton
# ═══════════════════════════════════════════════════════════════════════


class PerformanceMonitor:
    """Thread-safe in-process metrics store.

    All ``record_*`` methods use ``threading.Lock`` and are **synchronous**
    so they never block the async event loop on the hot path.  Snapshot
    reads acquire the same lock briefly.

    Usage (from anywhere in the app)::

        from app.services.performance_monitor import monitor
        monitor.record_llm_call("openai", "gpt-4o", ok=True, latency_ms=850)
        monitor.record_retry("openai", "gpt-4o")
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # provider:model_name → ModelMetrics
        self._models: dict[str, ModelMetrics] = {}
        self._stream = StreamMetrics()
        self._ws = WsMetrics()
        # Recent degradation events (ring buffer, last 50)
        self._degradations: list[DegradationEvent] = []
        self._max_degradation_events = 50
        # Startup timestamp
        self._started_at = time.time()
        # Global counters
        self._total_http_retries = 0
        self._total_tool_call_loops = 0
        self._total_tool_call_iterations = 0

    # ── LLM Call tracking (sync — hot-path safe) ───────────────────

    def record_llm_call(
        self, provider: str, model_name: str,
        ok: bool, latency_ms: float, error: str = "",
    ) -> None:
        key = f"{provider}:{model_name}"
        with self._lock:
            if key not in self._models:
                self._models[key] = ModelMetrics(provider=provider, model_name=model_name)
            m = self._models[key]
            m.total_calls += 1
            m.last_latency_ms = latency_ms
            m.latency.record(latency_ms)
            if ok:
                m.success += 1
            else:
                m.failures += 1
                if error:
                    m.last_error = error
                    m.last_error_at = time.time()

    def record_retry(self, provider: str, model_name: str) -> None:
        key = f"{provider}:{model_name}"
        with self._lock:
            if key not in self._models:
                self._models[key] = ModelMetrics(provider=provider, model_name=model_name)
            self._models[key].retries += 1
            self._total_http_retries += 1

    # ── Streaming tracking (sync) ──────────────────────────────────

    def record_stream_start(self) -> None:
        with self._lock:
            self._stream.total_streams += 1

    def record_ttft(self, ttft_ms: float) -> None:
        with self._lock:
            self._stream.last_ttft_ms = ttft_ms
            self._stream.ttft_hist.record(ttft_ms)

    def record_chunk(self, chunk_len: int, gap_ms: float = 0.0) -> None:
        with self._lock:
            self._stream.total_chunks += 1
            self._stream.total_bytes += chunk_len
            if gap_ms > 0:
                self._stream.last_chunk_gap_ms = gap_ms
                self._stream.chunk_gap_hist.record(gap_ms)

    # ── WebSocket tracking (sync) ──────────────────────────────────

    def record_broadcast(
        self, session_id: str, conn_count: int,
        ok_count: int, latency_ms: float, timeouts: int = 0,
    ) -> None:
        with self._lock:
            self._ws.total_broadcasts += 1
            self._ws.total_sends += conn_count
            self._ws.failures += (conn_count - ok_count)
            self._ws.timeouts += timeouts
            self._ws.broadcast_latency.record(latency_ms)

    # ── Degradation tracking (sync) ────────────────────────────────

    def record_degradation_enter(
        self, session_id: str, reason: str, failed_models: list[str],
    ) -> None:
        with self._lock:
            evt = DegradationEvent(
                session_id=session_id,
                entered_at=time.time(),
                reason=reason,
                failed_models=list(failed_models),
            )
            self._degradations.append(evt)
            if len(self._degradations) > self._max_degradation_events:
                self._degradations = self._degradations[-self._max_degradation_events:]

    def record_degradation_exit(
        self, session_id: str, recovery_attempts: int = 0,
    ) -> None:
        with self._lock:
            for evt in reversed(self._degradations):
                if evt.session_id == session_id and evt.exited_at == 0:
                    evt.exited_at = time.time()
                    evt.recovery_attempts = recovery_attempts
                    break

    # ── Tool-call loop tracking (sync) ─────────────────────────────

    def record_tool_call_loop(self, iterations: int) -> None:
        with self._lock:
            self._total_tool_call_loops += 1
            self._total_tool_call_iterations += iterations

    # ── Snapshot API (can be called from sync or async context) ────

    def snapshot(self) -> dict[str, Any]:
        """Return a complete metrics snapshot for the monitoring API."""
        with self._lock:
            models_snap: list[dict] = []
            for key, m in sorted(self._models.items()):
                success_rate = (m.success / max(1, m.total_calls)) * 100
                models_snap.append({
                    "key": key,
                    "provider": m.provider,
                    "model": m.model_name,
                    "totalCalls": m.total_calls,
                    "success": m.success,
                    "failures": m.failures,
                    "successRate": round(success_rate, 1),
                    "retries": m.retries,
                    "avgLatencyMs": round(m.latency.avg, 1),
                    "p50Ms": round(m.latency.percentile(50), 1),
                    "p95Ms": round(m.latency.percentile(95), 1),
                    "p99Ms": round(m.latency.percentile(99), 1),
                    "lastLatencyMs": round(m.last_latency_ms, 1),
                    "lastError": m.last_error[:200] if m.last_error else "",
                })

            stream_snap = {
                "totalStreams": self._stream.total_streams,
                "totalChunks": self._stream.total_chunks,
                "totalBytes": self._stream.total_bytes,
                "avgTtftMs": round(self._stream.ttft_hist.avg, 1),
                "p50TtftMs": round(self._stream.ttft_hist.percentile(50), 1),
                "p95TtftMs": round(self._stream.ttft_hist.percentile(95), 1),
                "lastTtftMs": round(self._stream.last_ttft_ms, 1),
                "avgChunkGapMs": round(self._stream.chunk_gap_hist.avg, 1),
            }

            ws_snap = {
                "totalBroadcasts": self._ws.total_broadcasts,
                "totalSends": self._ws.total_sends,
                "failures": self._ws.failures,
                "timeouts": self._ws.timeouts,
                "avgBroadcastMs": round(self._ws.broadcast_latency.avg, 1),
                "p95BroadcastMs": round(self._ws.broadcast_latency.percentile(95), 1),
            }

            # Recent degradation events (last 20)
            deg_snap: list[dict] = []
            for evt in self._degradations[-20:]:
                duration_s = (evt.exited_at - evt.entered_at) if evt.exited_at > 0 else (time.time() - evt.entered_at)
                deg_snap.append({
                    "sessionId": evt.session_id,
                    "reason": evt.reason[:200],
                    "failedModels": evt.failed_models,
                    "enteredAt": evt.entered_at,
                    "exitedAt": evt.exited_at if evt.exited_at > 0 else None,
                    "durationS": round(duration_s, 1),
                    "recoveryAttempts": evt.recovery_attempts,
                })

            uptime = time.time() - self._started_at

            return {
                "uptimeSeconds": round(uptime, 1),
                "global": {
                    "totalHttpRetries": self._total_http_retries,
                    "totalToolCallLoops": self._total_tool_call_loops,
                    "totalToolCallIterations": self._total_tool_call_iterations,
                    "avgIterationsPerLoop": (
                        round(self._total_tool_call_iterations / max(1, self._total_tool_call_loops), 2)
                    ),
                },
                "models": models_snap,
                "streaming": stream_snap,
                "websocket": ws_snap,
                "degradations": deg_snap,
            }

    def model_health(self) -> dict[str, Any]:
        """Lightweight health check snapshot (fast, no histograms)."""
        with self._lock:
            models_ok = 0
            models_degraded = 0
            for m in self._models.values():
                if m.last_error_at > time.time() - 300:  # error in last 5 min
                    models_degraded += 1
                else:
                    models_ok += 1

            active_degradations = sum(
                1 for evt in self._degradations if evt.exited_at == 0
            )

            return {
                "status": "degraded" if active_degradations > 0 else "healthy",
                "uptimeSeconds": round(time.time() - self._started_at, 1),
                "modelsHealthy": models_ok,
                "modelsDegraded": models_degraded,
                "activeDegradations": active_degradations,
                "totalHttpRetries": self._total_http_retries,
            }


# ── Module-level singleton ─────────────────────────────────────────
monitor = PerformanceMonitor()
