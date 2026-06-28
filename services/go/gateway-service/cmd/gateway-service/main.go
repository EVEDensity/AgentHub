package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
)

type ServiceProfile struct {
	Service          string          `json:"service"`
	Layer            string          `json:"layer"`
	Responsibilities []string        `json:"responsibilities"`
	DependsOn        []string        `json:"depends_on"`
	SampleEvent      events.Envelope `json:"sample_event"`
	PublishSubject   string          `json:"publish_subject"`
}

type PublishMessageRequest struct {
	TenantID  string         `json:"tenant_id"`
	SessionID string         `json:"session_id"`
	TraceID   string         `json:"trace_id"`
	MessageID string         `json:"message_id"`
	ActorID   string         `json:"actor_id"`
	Content   string         `json:"content"`
	Metadata  map[string]any `json:"metadata"`
}

type PermissionRequestInput struct {
	TenantID    string         `json:"tenant_id"`
	SessionID   string         `json:"session_id"`
	TraceID     string         `json:"trace_id"`
	RequestID   string         `json:"request_id"`
	ActorID     string         `json:"actor_id"`
	ToolName    string         `json:"tool_name"`
	RiskLevel   string         `json:"risk_level"`
	Reason      string         `json:"reason"`
	TimeoutSecs int            `json:"timeout_seconds"`
	Arguments   map[string]any `json:"arguments"`
}

type PublishResult struct {
	Published bool            `json:"published"`
	Subject   string          `json:"subject"`
	Event     events.Envelope `json:"event"`
}

func main() {
	natsURL := getenv("NATS_URL", "nats://127.0.0.1:4222")
	bus, err := eventbus.Connect(natsURL)
	if err != nil {
		log.Fatalf("connect event bus: %v", err)
	}
	defer bus.Close()

	profile := ServiceProfile{
		Service: "gateway-service",
		Layer:   "go-ingress",
		Responsibilities: []string{
			"http api ingress",
			"websocket gateway",
			"sse fallback",
			"rate limiting",
			"connection registry",
			"publish envelopes to NATS",
			"publish tool permission requests",
		},
		DependsOn:      []string{"redis", "nats", "session-service", "stream-delivery-service", "tool-permission-service"},
		PublishSubject: eventbus.SessionEventsSubject,
		SampleEvent: events.NewEnvelope(
			events.EventSessionMessageReceived,
			"tenant-demo",
			"session-demo",
			"trace-demo",
			events.Producer{Service: "gateway-service", Instance: "local"},
			map[string]any{"content": "hello"},
		),
	}
	profile.SampleEvent.EventID = "evt-demo-0001"

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/profile", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(profile)
	})
	mux.HandleFunc("/publish", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		var req PublishMessageRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid json body"})
			return
		}
		if req.TenantID == "" || req.SessionID == "" || req.TraceID == "" || req.Content == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id, session_id, trace_id, content are required"})
			return
		}

		event := events.NewEnvelope(
			events.EventSessionMessageReceived,
			req.TenantID,
			req.SessionID,
			req.TraceID,
			events.Producer{Service: "gateway-service", Instance: getenv("HOSTNAME", "local")},
			map[string]any{
				"content":  req.Content,
				"metadata": req.Metadata,
			},
		)
		event.EventID = fallback(req.MessageID, "evt-"+time.Now().UTC().Format("20060102T150405.000000000Z"))
		event.MessageID = req.MessageID
		event.ActorID = req.ActorID
		event.Routing = &events.Routing{Channel: "session", PartitionKey: req.SessionID, Priority: events.PriorityNormal}

		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		if err := bus.PublishEnvelope(ctx, eventbus.SessionEventsSubject, event); err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(PublishResult{Published: true, Subject: eventbus.SessionEventsSubject, Event: event})
	})
	mux.HandleFunc("/permissions/request", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		var req PermissionRequestInput
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid json body"})
			return
		}
		if req.TenantID == "" || req.SessionID == "" || req.TraceID == "" || req.RequestID == "" || req.ToolName == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id, session_id, trace_id, request_id, tool_name are required"})
			return
		}

		event := events.NewEnvelope(
			events.EventToolPermissionRequested,
			req.TenantID,
			req.SessionID,
			req.TraceID,
			events.Producer{Service: "gateway-service", Instance: getenv("HOSTNAME", "local")},
			map[string]any{
				"request_id":      req.RequestID,
				"tool_name":       req.ToolName,
				"risk_level":      fallback(req.RiskLevel, "normal"),
				"reason":          req.Reason,
				"timeout_seconds": fallbackInt(req.TimeoutSecs, 30),
				"arguments":       req.Arguments,
			},
		)
		event.EventID = "perm-request-" + req.RequestID
		event.MessageID = req.RequestID
		event.ActorID = req.ActorID
		event.Routing = &events.Routing{Channel: "permission", PartitionKey: req.RequestID, Priority: events.PriorityHigh}

		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		if err := bus.PublishEnvelope(ctx, eventbus.ToolPermissionRequestsSubject, event); err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(PublishResult{Published: true, Subject: eventbus.ToolPermissionRequestsSubject, Event: event})
	})

	addr := getenv("GATEWAY_ADDR", ":8081")
	log.Printf("gateway-service listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, mux))
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func fallback(primary, secondary string) string {
	if primary != "" {
		return primary
	}
	return secondary
}

func fallbackInt(primary, secondary int) int {
	if primary > 0 {
		return primary
	}
	return secondary
}
