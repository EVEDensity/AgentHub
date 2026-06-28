package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"sync"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
)

type EventBuffer struct {
	mu    sync.RWMutex
	items []events.Envelope
	limit int
}

func NewEventBuffer(limit int) *EventBuffer {
	return &EventBuffer{limit: limit, items: make([]events.Envelope, 0, limit)}
}

func (b *EventBuffer) Add(event events.Envelope) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if len(b.items) >= b.limit {
		b.items = append(b.items[1:], event)
		return
	}
	b.items = append(b.items, event)
}

func (b *EventBuffer) Snapshot() []events.Envelope {
	b.mu.RLock()
	defer b.mu.RUnlock()
	out := make([]events.Envelope, len(b.items))
	copy(out, b.items)
	return out
}

func (b *EventBuffer) Len() int {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return len(b.items)
}

func main() {
	resolvedBuffer := NewEventBuffer(100)
	auditBuffer := NewEventBuffer(100)

	natsURL := getenv("NATS_URL", "nats://127.0.0.1:4222")
	bus, err := eventbus.Connect(natsURL)
	if err != nil {
		log.Fatalf("connect event bus: %v", err)
	}
	defer bus.Close()

	if _, err := bus.Subscribe(eventbus.ToolPermissionResolvedSubject, func(env events.Envelope) {
		resolvedBuffer.Add(env)
		log.Printf("resolved event request=%s decision=%v", env.MessageID, env.Payload["decision"])
	}); err != nil {
		log.Fatalf("subscribe resolved events: %v", err)
	}

	if _, err := bus.Subscribe(eventbus.AuditSecurityEventsSubject, func(env events.Envelope) {
		auditBuffer.Add(env)
		log.Printf("audit event request=%s category=%v", env.MessageID, env.Payload["category"])
	}); err != nil {
		log.Fatalf("subscribe audit events: %v", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/events/resolved", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"subject": eventbus.ToolPermissionResolvedSubject,
			"count":   resolvedBuffer.Len(),
			"events":  resolvedBuffer.Snapshot(),
		})
	})
	mux.HandleFunc("/events/audit", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"subject": eventbus.AuditSecurityEventsSubject,
			"count":   auditBuffer.Len(),
			"events":  auditBuffer.Snapshot(),
		})
	})

	addr := getenv("AUDIT_ADDR", ":8087")
	log.Printf("audit-log-service listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, mux))
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
