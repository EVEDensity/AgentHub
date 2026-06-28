from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class EventType(str, Enum):
    SESSION_MESSAGE_RECEIVED = "session.message.received"
    SESSION_STREAM_CHUNK = "session.stream.chunk"
    SESSION_STREAM_FLUSH = "session.stream.flush"
    SESSION_STREAM_COMPLETE = "session.stream.complete"
    AGENT_REACT_TRANSITION = "agent.react.transition"
    AGENT_RUNTIME_DISPATCH = "agent.runtime.dispatch"
    AGENT_RUNTIME_RESULT = "agent.runtime.result"
    TOOL_PERMISSION_REQUESTED = "tool.permission.requested"
    TOOL_PERMISSION_RESOLVED = "tool.permission.resolved"
    RETRIEVAL_QUERY_REQUESTED = "retrieval.query.requested"
    RETRIEVAL_QUERY_COMPLETED = "retrieval.query.completed"
    RETRIEVAL_FUSION_COMPLETED = "retrieval.fusion.completed"
    AUDIT_SECURITY_EVENT = "audit.security.event"


class Producer(BaseModel):
    service: str
    instance: str
    region: str | None = None


class Routing(BaseModel):
    channel: str | None = None
    partition_key: str | None = None
    priority: Priority = Priority.NORMAL


class EventEnvelope(BaseModel):
    event_id: str = Field(min_length=8)
    event_type: EventType
    event_version: int = 1
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str
    tenant_id: str
    session_id: str
    message_id: str | None = None
    actor_id: str | None = None
    producer: Producer
    routing: Routing | None = None
    payload: dict[str, Any]


__all__ = [
    "Priority",
    "EventType",
    "Producer",
    "Routing",
    "EventEnvelope",
]
