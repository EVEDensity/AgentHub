package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
)

type ReactStage struct{ Name, Description string }
type ServiceProfile struct{ Service, SubscribedTo, PublishesTo, PermissionRequestsTo string; SampleEvent events.Envelope; BufferedEvents, BufferedPermissionEvents, BufferedPendingApprovals int }
type EventBuffer struct{ mu sync.RWMutex; items []events.Envelope; limit int }
type PendingApproval struct{ SessionEvent, PermissionEvent events.Envelope; DetectedTool, DetectedRisk, DetectedReason string; CreatedAt time.Time }
type PendingApprovalStore struct{ mu sync.RWMutex; items map[string]PendingApproval }

func NewEventBuffer(n int) *EventBuffer { return &EventBuffer{limit: n, items: make([]events.Envelope, 0, n)} }
func NewPendingApprovalStore() *PendingApprovalStore { return &PendingApprovalStore{items: map[string]PendingApproval{}} }
func (b *EventBuffer) Add(e events.Envelope) { b.mu.Lock(); defer b.mu.Unlock(); if len(b.items) >= b.limit { b.items = append(b.items[1:], e); return }; b.items = append(b.items, e) }
func (b *EventBuffer) Snapshot() []events.Envelope { b.mu.RLock(); defer b.mu.RUnlock(); out := make([]events.Envelope, len(b.items)); copy(out, b.items); return out }
func (b *EventBuffer) Len() int { b.mu.RLock(); defer b.mu.RUnlock(); return len(b.items) }
func (s *PendingApprovalStore) Put(id string, a PendingApproval) { s.mu.Lock(); defer s.mu.Unlock(); s.items[id] = a }
func (s *PendingApprovalStore) Get(id string) (PendingApproval, bool) { s.mu.RLock(); defer s.mu.RUnlock(); a, ok := s.items[id]; return a, ok }
func (s *PendingApprovalStore) Delete(id string) { s.mu.Lock(); defer s.mu.Unlock(); delete(s.items, id) }
func (s *PendingApprovalStore) Len() int { s.mu.RLock(); defer s.mu.RUnlock(); return len(s.items) }
func (s *PendingApprovalStore) Snapshot() map[string]PendingApproval { s.mu.RLock(); defer s.mu.RUnlock(); out := make(map[string]PendingApproval, len(s.items)); for k, v := range s.items { out[k] = v }; return out }

func main() {
	stages := []ReactStage{{"ingest", "validate and normalize message"}, {"classify", "route to router/planner/search flow"}, {"retrieve", "trigger deepsearch pipeline"}, {"plan", "decide next agent actions"}, {"act", "dispatch tools or model runtimes"}, {"observe", "collect runtime observations"}, {"reflect", "critic/reasoning checkpoint"}, {"continue_or_finish", "budget-aware continuation decision"}, {"synthesize", "build grounded answer"}, {"stream_back", "emit aggregated output"}}
	buffer, perms, pending := NewEventBuffer(50), NewEventBuffer(50), NewPendingApprovalStore()
	bus, err := eventbus.Connect(getenv("NATS_URL", "nats://127.0.0.1:4222")); if err != nil { log.Fatalf("connect event bus: %v", err) }; defer bus.Close()
	if _, err := bus.Subscribe(eventbus.SessionEventsSubject, func(env events.Envelope) {
		buffer.Add(env); ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second); defer cancel()
		if p, a, ok := buildPermissionRequest(env); ok {
			if err := publishLifecycle(ctx, bus, buildWaiting(env, a), nil); err != nil { log.Printf("publish waiting failed: %v", err); return }
			if err := bus.PublishEnvelope(ctx, eventbus.ToolPermissionRequestsSubject, p); err != nil { log.Printf("publish permission failed: %v", err); return }
			perms.Add(p); pending.Put(p.MessageID, a); return
		}
		if err := publishLifecycle(ctx, bus, buildResumed(env, "orchestrator received", 1, "resumed"), nil); err != nil { log.Printf("publish stream failed: %v", err) }
	}); err != nil { log.Fatalf("subscribe session events: %v", err) }
	if _, err := bus.Subscribe(eventbus.ToolPermissionResolvedSubject, func(env events.Envelope) {
		if env.EventType != events.EventToolPermissionResolved { return }
		reqID, decision := s(env.Payload, "request_id"), strings.ToLower(s(env.Payload, "decision"))
		a, ok := pending.Get(reqID); if !ok { return }
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second); defer cancel()
		if decision == "approved" {
			if err := publishLifecycle(ctx, bus, buildResumed(a.SessionEvent, "permission approved, resuming execution", 2, "approved"), nil); err != nil { log.Printf("publish resumed failed: %v", err); return }
		} else {
			errEnv := buildError(a.SessionEvent, a, decision)
			if err := publishLifecycle(ctx, bus, buildDenied(a.SessionEvent, a, decision), &errEnv); err != nil { log.Printf("publish denied failed: %v", err); return }
		}
		pending.Delete(reqID)
	}); err != nil { log.Fatalf("subscribe permission resolved: %v", err) }
	profile := ServiceProfile{Service: "realtime-orchestrator", SubscribedTo: eventbus.SessionEventsSubject, PublishesTo: eventbus.StreamEventsSubject, PermissionRequestsTo: eventbus.ToolPermissionRequestsSubject, SampleEvent: events.NewEnvelope(events.EventAgentReactTransition, "tenant-demo", "session-demo", "trace-demo", events.Producer{Service: "realtime-orchestrator", Instance: "local"}, map[string]any{"from_state": "classify", "to_state": "retrieve"})}; profile.SampleEvent.EventID = "evt-react-0001"
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK); _, _ = w.Write([]byte("ok")) })
	mux.HandleFunc("/react/stages", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); _ = json.NewEncoder(w).Encode(stages) })
	mux.HandleFunc("/profile", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); profile.BufferedEvents, profile.BufferedPermissionEvents, profile.BufferedPendingApprovals = buffer.Len(), perms.Len(), pending.Len(); _ = json.NewEncoder(w).Encode(profile) })
	mux.HandleFunc("/events/recent", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); _ = json.NewEncoder(w).Encode(map[string]any{"subject": eventbus.SessionEventsSubject, "count": buffer.Len(), "events": buffer.Snapshot()}) })
	mux.HandleFunc("/permissions/recent", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); _ = json.NewEncoder(w).Encode(map[string]any{"subject": eventbus.ToolPermissionRequestsSubject, "count": perms.Len(), "events": perms.Snapshot()}) })
	mux.HandleFunc("/permissions/pending", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); _ = json.NewEncoder(w).Encode(map[string]any{"count": pending.Len(), "pending": pending.Snapshot()}) })
	addr := getenv("ORCHESTRATOR_ADDR", ":8082"); log.Printf("realtime-orchestrator listening on %s", addr); log.Fatal(http.ListenAndServe(addr, mux))
}

func buildPermissionRequest(env events.Envelope) (events.Envelope, PendingApproval, bool) {
	tool, reason, risk, ok := risky(env); if !ok { return events.Envelope{}, PendingApproval{}, false }
	reqID := fallback(env.MessageID, env.EventID) + "-perm"
	p := events.NewEnvelope(events.EventToolPermissionRequested, env.TenantID, env.SessionID, env.TraceID, events.Producer{Service: "realtime-orchestrator", Instance: getenv("HOSTNAME", "local")}, map[string]any{"request_id": reqID, "tool_name": tool, "risk_level": risk, "reason": reason, "timeout_seconds": 60, "arguments": map[string]any{"source_event_id": env.EventID, "content": s(env.Payload, "content"), "metadata": env.Payload["metadata"]}})
	p.EventID, p.MessageID, p.ActorID = "perm-auto-"+reqID, reqID, env.ActorID; p.Routing = &events.Routing{Channel: "permission", PartitionKey: reqID, Priority: events.PriorityHigh}
	return p, PendingApproval{SessionEvent: env, PermissionEvent: p, DetectedTool: tool, DetectedRisk: risk, DetectedReason: reason, CreatedAt: time.Now().UTC()}, true
}
func buildWaiting(env events.Envelope, a PendingApproval) events.Envelope { return chunk(env, "stream-wait-"+env.EventID, 1, fmt.Sprintf("waiting for permission approval: %s", a.DetectedReason), true, "waiting_permission", a.PermissionEvent.MessageID, events.PriorityHigh) }
func buildDenied(env events.Envelope, a PendingApproval, d string) events.Envelope { return chunk(env, "stream-denied-"+env.EventID, 2, fmt.Sprintf("execution blocked: permission %s for %s", d, a.DetectedTool), false, "permission_denied", a.PermissionEvent.MessageID, events.PriorityHigh) }
func buildResumed(env events.Envelope, prefix string, seq int, status string) events.Envelope { return chunk(env, "stream-"+env.EventID, seq, fmt.Sprintf("%s: %v", prefix, env.Payload["content"]), false, status, "", events.PriorityNormal) }
func buildError(env events.Envelope, a PendingApproval, d string) events.Envelope {
	e := events.NewEnvelope(events.EventSessionStreamError, env.TenantID, env.SessionID, env.TraceID, events.Producer{Service: "realtime-orchestrator", Instance: getenv("HOSTNAME", "local")}, map[string]any{"stream_id": "stream-error-" + env.EventID, "status": "error", "request_id": a.PermissionEvent.MessageID, "error": fmt.Sprintf("permission %s for %s", d, a.DetectedTool)})
	e.EventID, e.MessageID, e.ActorID = "stream-error-"+env.EventID, env.MessageID, env.ActorID; e.Routing = &events.Routing{Channel: "stream", PartitionKey: env.SessionID, Priority: events.PriorityHigh}; return e
}
func publishLifecycle(ctx context.Context, bus *eventbus.Client, c events.Envelope, e *events.Envelope) error {
	f := sibling(events.EventSessionStreamFlush, c, c.EventID+"-flush", c.MessageID, "flush")
	d := sibling(events.EventSessionStreamComplete, c, c.EventID+"-complete", c.MessageID, s(c.Payload, "status"))
	if err := bus.PublishEnvelope(ctx, eventbus.StreamEventsSubject, c); err != nil { return err }
	if err := bus.PublishEnvelope(ctx, eventbus.StreamEventsSubject, f); err != nil { return err }
	if e != nil { if err := bus.PublishEnvelope(ctx, eventbus.StreamEventsSubject, *e); err != nil { return err } }
	return bus.PublishEnvelope(ctx, eventbus.StreamEventsSubject, d)
}
func chunk(env events.Envelope, id string, seq int, content string, thinking bool, status, reqID string, p events.Priority) events.Envelope {
	e := events.NewEnvelope(events.EventSessionStreamChunk, env.TenantID, env.SessionID, env.TraceID, events.Producer{Service: "realtime-orchestrator", Instance: getenv("HOSTNAME", "local")}, map[string]any{"stream_id": id, "sequence": seq, "content": content, "content_type": "text/plain", "agent_id": "router-agent", "is_thinking": thinking, "status": status, "request_id": reqID})
	e.EventID, e.MessageID, e.ActorID = id, env.MessageID, env.ActorID; e.Routing = &events.Routing{Channel: "stream", PartitionKey: env.SessionID, Priority: p}; return e
}
func sibling(t events.EventType, base events.Envelope, id, msg, status string) events.Envelope {
	e := events.NewEnvelope(t, base.TenantID, base.SessionID, base.TraceID, events.Producer{Service: "realtime-orchestrator", Instance: getenv("HOSTNAME", "local")}, map[string]any{"stream_id": base.Payload["stream_id"], "status": status})
	e.EventID, e.MessageID, e.ActorID = id, msg, base.ActorID; e.Routing = &events.Routing{Channel: "stream", PartitionKey: base.SessionID, Priority: events.PriorityNormal}; return e
}
func risky(env events.Envelope) (string, string, string, bool) {
	c := strings.ToLower(s(env.Payload, "content") + " " + js(env.Payload["metadata"]))
	switch {
	case has(c, []string{"rm -rf", "delete production", "drop database", "truncate table"}): return "shell", "destructive shell/database intent detected", "critical", true
	case has(c, []string{"docker compose", "kubectl", "terraform apply", "deploy"}): return "shell", "deployment or infrastructure command intent detected", "high", true
	case has(c, []string{"read secrets", "export token", "access prod env"}): return "secrets", "sensitive credential access intent detected", "high", true
	default: return "", "", "", false
	}
}
func has(input string, xs []string) bool { for _, x := range xs { if strings.Contains(input, x) { return true } }; return false }
func s(m map[string]any, k string) string { v, ok := m[k]; if !ok || v == nil { return "" }; if x, ok := v.(string); ok { return x }; return fmt.Sprintf("%v", v) }
func js(v any) string { if v == nil { return "" }; b, err := json.Marshal(v); if err != nil { return "" }; return string(b) }
func fallback(a, b string) string { if a != "" { return a }; return b }
func getenv(k, d string) string { if v := os.Getenv(k); v != "" { return v }; return d }
