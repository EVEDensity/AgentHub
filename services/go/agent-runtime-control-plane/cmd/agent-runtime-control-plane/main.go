package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
)

type RuntimePools struct {
	Pools []map[string]any `json:"pools"`
}

type EventBuffer struct {
	mu sync.RWMutex
	items []events.Envelope
	limit int
}

func NewEventBuffer(limit int) *EventBuffer { return &EventBuffer{items: make([]events.Envelope, 0, limit), limit: limit} }
func (b *EventBuffer) Add(e events.Envelope) { b.mu.Lock(); defer b.mu.Unlock(); if len(b.items) >= b.limit { b.items = append(b.items[1:], e); return }; b.items = append(b.items, e) }
func (b *EventBuffer) Snapshot() []events.Envelope { b.mu.RLock(); defer b.mu.RUnlock(); out := make([]events.Envelope, len(b.items)); copy(out, b.items); return out }
func (b *EventBuffer) Len() int { b.mu.RLock(); defer b.mu.RUnlock(); return len(b.items) }

func main() {
	pools := RuntimePools{Pools: []map[string]any{{"name": "llm-online", "max_concurrency": 300, "kind": "model"}, {"name": "tool-high", "max_concurrency": 150, "kind": "tool"}, {"name": "python-offline", "max_concurrency": 200, "kind": "offline"}}}
	dispatchBuf, resultBuf := NewEventBuffer(100), NewEventBuffer(100)
	bus, err := eventbus.Connect(getenv("NATS_URL", "nats://127.0.0.1:4222"))
	if err != nil { log.Fatalf("connect event bus: %v", err) }
	defer bus.Close()
	if _, err := bus.Subscribe(eventbus.AgentRuntimeDispatchSubject, func(env events.Envelope) {
		dispatchBuf.Add(env)
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		result := events.NewEnvelope(events.EventAgentRuntimeResult, env.TenantID, env.SessionID, env.TraceID, events.Producer{Service: "agent-runtime-control-plane", Instance: getenv("HOSTNAME", "local")}, map[string]any{"dispatch_event_id": env.EventID, "runtime_id": stringValue(env.Payload, "runtime_id"), "pool": stringValue(env.Payload, "pool"), "status": "completed", "output": "runtime executed request", "tool_name": stringValue(env.Payload, "tool_name")})
		result.EventID = "runtime-result-" + env.EventID
		result.MessageID = env.MessageID
		result.ActorID = env.ActorID
		result.Routing = &events.Routing{Channel: "runtime", PartitionKey: env.SessionID, Priority: events.PriorityNormal}
		if err := bus.PublishEnvelope(ctx, eventbus.AgentRuntimeResultsSubject, result); err != nil { log.Printf("publish runtime result failed: %v", err); return }
		resultBuf.Add(result)
	}) ; err != nil { log.Fatalf("subscribe runtime dispatch: %v", err) }

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK); _, _ = w.Write([]byte("ok")) })
	mux.HandleFunc("/runtime/pools", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); _ = json.NewEncoder(w).Encode(pools) })
	mux.HandleFunc("/runtime/dispatches", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); _ = json.NewEncoder(w).Encode(map[string]any{"subject": eventbus.AgentRuntimeDispatchSubject, "count": dispatchBuf.Len(), "events": dispatchBuf.Snapshot()}) })
	mux.HandleFunc("/runtime/results", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); _ = json.NewEncoder(w).Encode(map[string]any{"subject": eventbus.AgentRuntimeResultsSubject, "count": resultBuf.Len(), "events": resultBuf.Snapshot()}) })
	addr := getenv("RUNTIME_ADDR", ":8085")
	log.Printf("agent-runtime-control-plane listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, mux))
}

func stringValue(payload map[string]any, key string) string {
	v, ok := payload[key]
	if !ok || v == nil { return "" }
	if s, ok := v.(string); ok { return s }
	return ""
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" { return v }
	return fallback
}
