package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/db"
	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
	"github.com/agenthub/platform/shared/obs"
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

	shutdown, err := obs.InitTracer(context.Background(), getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""), "audit-log-service")
	if err != nil {
		log.Fatalf("init tracer: %v", err)
	}
	defer shutdown(context.Background())

	dsn := getenv("DATABASE_DSN", "postgres://agenthub:agenthub@localhost:5432/agenthub?sslmode=disable")
	pool, err := db.Connect(context.Background(), dsn)
	if err != nil {
		log.Fatalf("connect db: %v", err)
	}
	defer pool.Close()
	if err := pool.Migrate(context.Background()); err != nil {
		log.Fatalf("run db migrations: %v", err)
	}
	log.Printf("audit-log-service db migrated")

	instance := getenv("HOSTNAME", "local")
	if _, err := bus.Subscribe("audit-resolved-"+instance, eventbus.ToolPermissionResolvedSubject, func(env events.Envelope) {
		obs.IncEventReceived("audit-log-service", string(env.EventType))
		resolvedBuffer.Add(env)
		persistAudit(context.Background(), pool, env, "tool_permission")
		log.Printf("resolved event request=%s decision=%v", env.MessageID, env.Payload["decision"])
	}); err != nil {
		log.Fatalf("subscribe resolved events: %v", err)
	}

	if _, err := bus.Subscribe("audit-security-"+instance, eventbus.AuditSecurityEventsSubject, func(env events.Envelope) {
		obs.IncEventReceived("audit-log-service", string(env.EventType))
		auditBuffer.Add(env)
		category := "security"
		if c, ok := env.Payload["category"].(string); ok && c != "" {
			category = c
		}
		persistAudit(context.Background(), pool, env, category)
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
	mux.HandleFunc("/events/persisted", func(w http.ResponseWriter, r *http.Request) {
		tenantID := r.URL.Query().Get("tenant_id")
		limit := 50
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		rows, err := pool.Query(ctx, `
			SELECT id, tenant_id, session_id, event_type, category, actor_id, payload_json, created_at
			FROM platform_audit_events
			WHERE ($1 = '' OR tenant_id = $1)
			ORDER BY created_at DESC
			LIMIT $2`, tenantID, limit)
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		defer rows.Close()
		type row struct {
			ID        string `json:"id"`
			TenantID  string `json:"tenant_id"`
			SessionID string `json:"session_id"`
			EventType string `json:"event_type"`
			Category  string `json:"category"`
			ActorID   string `json:"actor_id"`
			Payload   string `json:"payload_json"`
			CreatedAt string `json:"created_at"`
		}
		out := make([]row, 0, limit)
		for rows.Next() {
			var r row
			if err := rows.Scan(&r.ID, &r.TenantID, &r.SessionID, &r.EventType, &r.Category, &r.ActorID, &r.Payload, &r.CreatedAt); err == nil {
				out = append(out, r)
			}
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"count": len(out), "events": out})
	})
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		obs.MetricsHandler().ServeHTTP(w, r)
	})

	addr := getenv("AUDIT_ADDR", ":8087")
	log.Printf("audit-log-service listening on %s", addr)
	handler := obs.Middleware("audit-log-service", mux)
	log.Fatal(http.ListenAndServe(addr, handler))
}

// persistAudit inserts an envelope into platform_audit_events. Failures are
// logged but never block the subscriber (audit is best-effort on the hot path;
// the in-memory buffer + JetStream durable consumer provide redundancy).
func persistAudit(ctx context.Context, pool *db.Pool, env events.Envelope, category string) {
	if pool == nil {
		return
	}
	payload, _ := json.Marshal(env.Payload)
	insCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	_, err := pool.Exec(insCtx, `
		INSERT INTO platform_audit_events (id, tenant_id, session_id, trace_id, event_type, category, actor_id, payload_json)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
		ON CONFLICT (id) DO NOTHING`,
		env.EventID, env.TenantID, env.SessionID, env.TraceID, string(env.EventType), category, env.ActorID, string(payload))
	if err != nil {
		log.Printf("persist audit event failed id=%s: %v", env.EventID, err)
	}
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
