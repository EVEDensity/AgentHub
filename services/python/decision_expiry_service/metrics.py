"""Prometheus projection for process-local Decision expiry supervision."""

from __future__ import annotations

from datetime import datetime

from app.services.decision_expiry_supervisor import DecisionExpirySupervisorSnapshot

from .runtime import DecisionExpiryServiceRuntime

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def render_metrics(runtime: DecisionExpiryServiceRuntime | None) -> str:
    snapshot = runtime.snapshot if runtime is not None else DecisionExpirySupervisorSnapshot()
    healthy = int(runtime is not None and runtime.healthy)
    ready = int(runtime is not None and runtime.ready)
    last_success = _timestamp_seconds(snapshot.last_success_at)
    metrics = (
        (
            "agenthub_decision_expiry_process_healthy",
            "gauge",
            "Whether the Decision expiry worker task is alive.",
            healthy,
        ),
        (
            "agenthub_decision_expiry_ready",
            "gauge",
            "Whether the worker completed a successful database poll.",
            ready,
        ),
        (
            "agenthub_decision_expiry_polls_total",
            "counter",
            "Total Decision expiry command polls in this process.",
            snapshot.polls,
        ),
        (
            "agenthub_decision_expiry_decisions_expired_total",
            "counter",
            "Total Decisions expired by this process.",
            snapshot.expired,
        ),
        (
            "agenthub_decision_expiry_idle_polls_total",
            "counter",
            "Total successful polls that found no eligible Decision.",
            snapshot.idle_polls,
        ),
        (
            "agenthub_decision_expiry_failed_polls_total",
            "counter",
            "Total failed expiry command polls in this process.",
            snapshot.failed_polls,
        ),
        (
            "agenthub_decision_expiry_consecutive_failures",
            "gauge",
            "Current consecutive expiry command failure count.",
            snapshot.consecutive_failures,
        ),
        (
            "agenthub_decision_expiry_backoff_seconds",
            "gauge",
            "Current process-local delay before the next poll.",
            snapshot.current_delay_seconds,
        ),
        (
            "agenthub_decision_expiry_last_success_timestamp_seconds",
            "gauge",
            "Unix timestamp of the latest successful database poll.",
            last_success,
        ),
    )
    lines: list[str] = []
    for name, metric_type, help_text, value in metrics:
        lines.extend(
            (
                f"# HELP {name} {help_text}",
                f"# TYPE {name} {metric_type}",
                f"{name} {value}",
            )
        )
    return "\n".join(lines) + "\n"


def _timestamp_seconds(value: str | None) -> float:
    if value is None:
        return 0.0
    return datetime.fromisoformat(value).timestamp()


__all__ = ["PROMETHEUS_CONTENT_TYPE", "render_metrics"]
