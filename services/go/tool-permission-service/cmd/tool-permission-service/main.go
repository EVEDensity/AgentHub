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
	"github.com/agenthub/platform/shared/state"
)

type PermissionPolicy struct {
	Storage      string   `json:"storage"`
	Inputs       []string `json:"inputs"`
	Outputs      []string `json:"outputs"`
	SubscribeTo  string   `json:"subscribe_to"`
	ResolvedTo   string   `json:"resolved_to"`
	AuditTo      string   `json:"audit_to"`
}

type PermissionRequest struct {
	TenantID    string         `json:"tenant_id"`
	RequestID   string         `json:"request_id"`
	SessionID   string         `json:"session_id"`
	TraceID     string         `json:"trace_id"`
	ActorID     string         `json:"actor_id"`
	ToolName    string         `json:"tool_name"`
	RiskLevel   string         `json:"risk_level"`
	Reason      string         `json:"reason"`
	TimeoutSecs int            `json:"timeout_seconds"`
	Arguments   map[string]any `json:"arguments"`
}

type PermissionDecision struct {
	TenantID  string `json:"tenant_id"`
	RequestID string `json:"request_id"`
	Decision  string `json:"decision"`
	DecidedBy string `json:"decided_by"`
}

func main() {
	policy := PermissionPolicy{
		Storage:     "redis-cluster",
		Inputs:      []string{"tool.permission.requested"},
		Outputs:     []string{"tool.permission.resolved", "audit.security.event"},
		SubscribeTo: eventbus.ToolPermissionRequestsSubject,
		ResolvedTo:  eventbus.ToolPermissionResolvedSubject,
		AuditTo:     eventbus.AuditSecurityEventsSubject,
	}

	redisAddr := getenv("REDIS_ADDR", "127.0.0.1:6379")
	store := state.Connect(redisAddr)
	defer func() {
		if err := store.Close(); err != nil {
			log.Printf("close redis: %v", err)
		}
	}()

	natsURL := getenv("NATS_URL", "nats://127.0.0.1:4222")
	bus, err := eventbus.Connect(natsURL)
	if err != nil {
		log.Fatalf("connect event bus: %v", err)
	}
	defer bus.Close()

	if _, err := bus.Subscribe(eventbus.ToolPermissionRequestsSubject, func(env events.Envelope) {
		if env.EventType != events.EventToolPermissionRequested {
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		requestID := stringValue(env.Payload, "request_id", env.EventID)
		toolName := stringValue(env.Payload, "tool_name", "unknown")
		riskLevel := stringValue(env.Payload, "risk_level", "normal")
		reason := stringValue(env.Payload, "reason", "")
		arguments := jsonStringValue(env.Payload["arguments"])
		ttlSecs := intValue(env.Payload, "timeout_seconds", 30)
		key := state.PermissionKey(env.TenantID, requestID)
		if err := store.HSet(ctx, key,
			"session_id", env.SessionID,
			"trace_id", env.TraceID,
			"actor_id", env.ActorID,
			"tool_name", toolName,
			"risk_level", riskLevel,
			"reason", reason,
			"decision", "pending",
			"arguments", arguments,
		); err != nil {
			log.Printf("persist permission request failed: %v", err)
			return
		}
		_ = store.Expire(ctx, key, time.Duration(ttlSecs)*time.Second)
		log.Printf("persisted permission request request_id=%s tool=%s", requestID, toolName)
	}); err != nil {
		log.Fatalf("subscribe permission requests: %v", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/policy", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(policy)
	})
	mux.HandleFunc("/requests", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var req PermissionRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid json body"})
			return
		}
		if req.TenantID == "" || req.RequestID == "" || req.ToolName == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id, request_id, tool_name are required"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		key := state.PermissionKey(req.TenantID, req.RequestID)
		args, _ := json.Marshal(req.Arguments)
		ttl := time.Duration(req.TimeoutSecs) * time.Second
		if ttl <= 0 {
			ttl = 30 * time.Second
		}
		if err := store.HSet(ctx, key,
			"session_id", req.SessionID,
			"trace_id", req.TraceID,
			"actor_id", req.ActorID,
			"tool_name", req.ToolName,
			"risk_level", req.RiskLevel,
			"reason", req.Reason,
			"decision", "pending",
			"arguments", string(args),
		); err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		_ = store.Expire(ctx, key, ttl)
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "key": key, "ttl_seconds": int(ttl.Seconds())})
	})
	mux.HandleFunc("/requests/decision", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var req PermissionDecision
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid json body"})
			return
		}
		if req.TenantID == "" || req.RequestID == "" || req.Decision == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id, request_id, decision are required"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		key := state.PermissionKey(req.TenantID, req.RequestID)
		if err := store.HSet(ctx, key,
			"decision", req.Decision,
			"decided_by", req.DecidedBy,
			"decided_at", time.Now().UTC().Format(time.RFC3339),
		); err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		data, err := store.HGetAll(ctx, key)
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		resolvedEvent, auditEvent := buildDecisionEvents(req, data)
		if err := bus.PublishEnvelope(ctx, eventbus.ToolPermissionResolvedSubject, resolvedEvent); err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		if err := bus.PublishEnvelope(ctx, eventbus.AuditSecurityEventsSubject, auditEvent); err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"ok":             true,
			"key":            key,
			"decision":       req.Decision,
			"resolved_event": resolvedEvent.EventID,
			"audit_event":    auditEvent.EventID,
		})
	})
	mux.HandleFunc("/requests/get", func(w http.ResponseWriter, r *http.Request) {
		tenantID := r.URL.Query().Get("tenant_id")
		requestID := r.URL.Query().Get("request_id")
		if tenantID == "" || requestID == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id and request_id are required"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		key := state.PermissionKey(tenantID, requestID)
		data, err := store.HGetAll(ctx, key)
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"key": key, "data": data})
	})

	addr := getenv("PERMISSION_ADDR", ":8084")
	log.Printf("tool-permission-service listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, mux))
}

func buildDecisionEvents(req PermissionDecision, data map[string]string) (events.Envelope, events.Envelope) {
	traceID := data["trace_id"]
	if traceID == "" {
		traceID = req.RequestID
	}
	sessionID := data["session_id"]
	actorID := data["actor_id"]
	producer := events.Producer{Service: "tool-permission-service", Instance: getenv("HOSTNAME", "local")}
	resolved := events.NewEnvelope(
		events.EventToolPermissionResolved,
		req.TenantID,
		sessionID,
		traceID,
		producer,
		map[string]any{
			"request_id": req.RequestID,
			"tool_name":  data["tool_name"],
			"decision":   req.Decision,
			"decided_by": req.DecidedBy,
			"risk_level": data["risk_level"],
		},
	)
	resolved.EventID = "perm-resolved-" + req.RequestID
	resolved.ActorID = actorID
	resolved.MessageID = req.RequestID
	resolved.Routing = &events.Routing{Channel: "permission", PartitionKey: req.RequestID, Priority: events.PriorityHigh}

	audit := events.NewEnvelope(
		events.EventAuditSecurity,
		req.TenantID,
		sessionID,
		traceID,
		producer,
		map[string]any{
			"category":   "tool_permission",
			"request_id": req.RequestID,
			"tool_name":  data["tool_name"],
			"decision":   req.Decision,
			"decided_by": req.DecidedBy,
			"reason":     data["reason"],
		},
	)
	audit.EventID = "audit-perm-" + req.RequestID
	audit.ActorID = actorID
	audit.MessageID = req.RequestID
	audit.Routing = &events.Routing{Channel: "audit", PartitionKey: req.RequestID, Priority: events.PriorityHigh}
	return resolved, audit
}

func stringValue(payload map[string]any, key, fallback string) string {
	value, ok := payload[key]
	if !ok || value == nil {
		return fallback
	}
	if s, ok := value.(string); ok && s != "" {
		return s
	}
	return fallback
}

func intValue(payload map[string]any, key string, fallback int) int {
	value, ok := payload[key]
	if !ok || value == nil {
		return fallback
	}
	switch v := value.(type) {
	case float64:
		return int(v)
	case int:
		return v
	default:
		return fallback
	}
}

func jsonStringValue(value any) string {
	if value == nil {
		return "{}"
	}
	b, err := json.Marshal(value)
	if err != nil {
		return "{}"
	}
	return string(b)
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
