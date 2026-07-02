package events

import "time"

type Priority string

const (
	PriorityLow      Priority = "low"
	PriorityNormal   Priority = "normal"
	PriorityHigh     Priority = "high"
	PriorityCritical Priority = "critical"
)

type EventType string

const (
	EventSessionMessageReceived  EventType = "session.message.received"
	EventSessionStreamChunk      EventType = "session.stream.chunk"
	EventSessionStreamFlush      EventType = "session.stream.flush"
	EventSessionStreamComplete   EventType = "session.stream.complete"
	EventSessionStreamError      EventType = "session.stream.error"
	EventAgentReactTransition    EventType = "agent.react.transition"
	EventAgentRuntimeDispatch    EventType = "agent.runtime.dispatch"
	EventAgentRuntimeResult      EventType = "agent.runtime.result"
	EventToolPermissionRequested EventType = "tool.permission.requested"
	EventToolPermissionResolved  EventType = "tool.permission.resolved"
	EventRetrievalQueryRequested EventType = "retrieval.query.requested"
	EventRetrievalQueryCompleted EventType = "retrieval.query.completed"
	EventRetrievalFusionComplete EventType = "retrieval.fusion.completed"
	EventAuditSecurity           EventType = "audit.security.event"

	// Sprint D: Rust core → Go events (published by Rust async_nats, consumed by Go via core NATS).
	EventFanoutDelivered       EventType = "fanout.event.delivered"
	EventPatchMergeCompleted   EventType = "patch.merge.completed"
	EventMemoryCompactCompleted EventType = "memory.compact.completed"
)

type Producer struct {
	Service  string  `json:"service"`
	Instance string  `json:"instance"`
	Region   *string `json:"region,omitempty"`
}

type Routing struct {
	Channel      string   `json:"channel,omitempty"`
	PartitionKey string   `json:"partition_key,omitempty"`
	Priority     Priority `json:"priority,omitempty"`
}

type Envelope struct {
	EventID      string         `json:"event_id"`
	EventType    EventType      `json:"event_type"`
	EventVersion int            `json:"event_version"`
	OccurredAt   time.Time      `json:"occurred_at"`
	TraceID      string         `json:"trace_id"`
	TenantID     string         `json:"tenant_id"`
	SessionID    string         `json:"session_id"`
	MessageID    string         `json:"message_id,omitempty"`
	ActorID      string         `json:"actor_id,omitempty"`
	Producer     Producer       `json:"producer"`
	Routing      *Routing       `json:"routing,omitempty"`
	Payload      map[string]any `json:"payload"`
}

func NewEnvelope(eventType EventType, tenantID, sessionID, traceID string, producer Producer, payload map[string]any) Envelope {
	return Envelope{
		EventType:    eventType,
		EventVersion: 1,
		OccurredAt:   time.Now().UTC(),
		TraceID:      traceID,
		TenantID:     tenantID,
		SessionID:    sessionID,
		Producer:     producer,
		Payload:      payload,
	}
}
