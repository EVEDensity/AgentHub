package main

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/agenthub/platform/shared/db"
	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
	"github.com/agenthub/platform/shared/obs"
	"github.com/prometheus/client_golang/prometheus"
)

// ── Prometheus audit metrics ──────────────────────────────────────────

var (
	auditEventsPersisted = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "audit_events_persisted_total", Help: "Audit events written to PostgreSQL by category."},
		[]string{"category"},
	)
	auditBatchSize = prometheus.NewHistogram(
		prometheus.HistogramOpts{Name: "audit_batch_size", Help: "Number of events per batch flush.", Buckets: []float64{1, 5, 10, 25, 50, 100}},
	)
	auditBatchLatency = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{Name: "audit_batch_latency_seconds", Help: "Latency of batch flushes (ok / error)."},
		[]string{"status"},
	)
	auditBufferDepthGauge = prometheus.NewGauge(
		prometheus.GaugeOpts{Name: "audit_buffer_depth", Help: "Current pending events in the batch buffer."},
	)
)

func init() {
	obs.MustRegister(auditEventsPersisted, auditBatchSize, auditBatchLatency, auditBufferDepthGauge)
}

// Tracks the last-set buffer depth for the /events/stats endpoint without
// calling back into prometheus.
var lastBufferDepth int64

// ── In-memory ring buffers (debug / live inspection) ──────────────────

type eventBuffer struct {
	mu    sync.RWMutex
	items []events.Envelope
	limit int
}

func newEventBuffer(limit int) *eventBuffer {
	return &eventBuffer{limit: limit, items: make([]events.Envelope, 0, limit)}
}

func (b *eventBuffer) add(event events.Envelope) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if len(b.items) >= b.limit {
		b.items = append(b.items[1:], event)
		return
	}
	b.items = append(b.items, event)
}

func (b *eventBuffer) snapshot() []events.Envelope {
	b.mu.RLock()
	defer b.mu.RUnlock()
	out := make([]events.Envelope, len(b.items))
	copy(out, b.items)
	return out
}

func (b *eventBuffer) len() int {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return len(b.items)
}

// ── Batch flusher ─────────────────────────────────────────────────────

type pendingEvent struct {
	Envelope events.Envelope
	Category string
}

// auditBatchFlusher buffers events and writes them to PostgreSQL in
// batches. Flush triggers are: (a) a tick interval, (b) buffer reaching
// high-water mark. The batch INSERT uses a single multi-row statement to
// minimise round-trips.
type auditBatchFlusher struct {
	mu        sync.Mutex
	buf       []pendingEvent
	pool      *db.Pool
	flushSize int
	flushTick time.Duration
	done      chan struct{}
}

func newAuditBatchFlusher(pool *db.Pool, flushSize int, flushTick time.Duration) *auditBatchFlusher {
	return &auditBatchFlusher{
		buf:       make([]pendingEvent, 0, flushSize),
		pool:      pool,
		flushSize: flushSize,
		flushTick: flushTick,
		done:      make(chan struct{}),
	}
}

func (f *auditBatchFlusher) start() {
	go func() {
		ticker := time.NewTicker(f.flushTick)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				f.flush()
			case <-f.done:
				f.flush()
				return
			}
		}
	}()
}

func (f *auditBatchFlusher) stop() { close(f.done) }

func (f *auditBatchFlusher) enqueue(env events.Envelope, category string) {
	f.mu.Lock()
	f.buf = append(f.buf, pendingEvent{Envelope: env, Category: category})
	n := len(f.buf)
	f.mu.Unlock()

	auditBufferDepthGauge.Set(float64(n))
	atomic.StoreInt64(&lastBufferDepth, int64(n))

	if n >= f.flushSize {
		f.flush()
	}
}

func (f *auditBatchFlusher) flush() {
	f.mu.Lock()
	if len(f.buf) == 0 {
		f.mu.Unlock()
		return
	}
	batch := make([]pendingEvent, len(f.buf))
	copy(batch, f.buf)
	f.buf = f.buf[:0]
	f.mu.Unlock()

	auditBufferDepthGauge.Set(0)
	atomic.StoreInt64(&lastBufferDepth, 0)
	auditBatchSize.Observe(float64(len(batch)))

	start := time.Now()
	if err := persistBatch(context.Background(), f.pool, batch); err != nil {
		auditBatchLatency.WithLabelValues("error").Observe(time.Since(start).Seconds())
		log.Printf("audit batch flush FAIL (%d events): %v", len(batch), err)

		// Re-queue for retry with cap to avoid unbounded growth.
		f.mu.Lock()
		f.buf = append(batch, f.buf...)
		if len(f.buf) > f.flushSize*2 {
			f.buf = f.buf[len(f.buf)-f.flushSize:]
		}
		n := len(f.buf)
		f.mu.Unlock()
		auditBufferDepthGauge.Set(float64(n))
		atomic.StoreInt64(&lastBufferDepth, int64(n))
		return
	}
	auditBatchLatency.WithLabelValues("ok").Observe(time.Since(start).Seconds())

	counts := map[string]int{}
	for _, pe := range batch {
		counts[pe.Category]++
	}
	for cat, n := range counts {
		auditEventsPersisted.WithLabelValues(cat).Add(float64(n))
	}
	log.Printf("audit batch flushed %d events (categories: %v)", len(batch), counts)
}

// persistBatch runs a single multi-row INSERT for the whole batch.
func persistBatch(ctx context.Context, pool *db.Pool, batch []pendingEvent) error {
	if pool == nil || len(batch) == 0 {
		return nil
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	const cols = 8
	args := make([]any, 0, len(batch)*cols)
	phs := make([]string, 0, len(batch))
	for i, pe := range batch {
		off := i * cols
		phs = append(phs, fmt.Sprintf("($%d,$%d,$%d,$%d,$%d,$%d,$%d,$%d)",
			off+1, off+2, off+3, off+4, off+5, off+6, off+7, off+8))
		payload, _ := json.Marshal(pe.Envelope.Payload)
		args = append(args,
			pe.Envelope.EventID, pe.Envelope.TenantID, pe.Envelope.SessionID,
			pe.Envelope.TraceID, string(pe.Envelope.EventType), pe.Category,
			pe.Envelope.ActorID, string(payload),
		)
	}

	query := fmt.Sprintf(`
		INSERT INTO platform_audit_events (id, tenant_id, session_id, trace_id, event_type, category, actor_id, payload_json)
		VALUES %s
		ON CONFLICT (id) DO NOTHING`, join(phs, ", "))

	_, err := pool.Exec(ctx, query, args...)
	return err
}

// ── HTTP query helpers ─────────────────────────────────────────────────

type auditRow struct {
	ID        string `json:"id"`
	TenantID  string `json:"tenant_id"`
	SessionID string `json:"session_id"`
	TraceID   string `json:"trace_id"`
	EventType string `json:"event_type"`
	Category  string `json:"category"`
	ActorID   string `json:"actor_id"`
	Payload   string `json:"payload_json"`
	CreatedAt string `json:"created_at"`
}

// queryAudit builds and executes a parameterized query against
// platform_audit_events with the supplied filters.
func queryAudit(ctx context.Context, pool *db.Pool, filters map[string]string, limit, offset int, order string) ([]auditRow, error) {
	conds, args := []string{}, []any{}
	idx := 1

	add := func(col, op, val string) {
		conds = append(conds, fmt.Sprintf("%s %s $%d", col, op, idx))
		args = append(args, val)
		idx++
	}

	for _, f := range []struct{ col, key string }{
		{"tenant_id", "tenant_id"}, {"session_id", "session_id"},
		{"event_type", "event_type"}, {"category", "category"}, {"actor_id", "actor_id"},
	} {
		if v := filters[f.key]; v != "" {
			add(f.col, "=", v)
		}
	}
	if v := filters["since"]; v != "" {
		add("created_at", ">=", v)
	}
	if v := filters["until"]; v != "" {
		add("created_at", "<=", v)
	}

	where := ""
	if len(conds) > 0 {
		where = "WHERE " + join(conds, " AND ")
	}
	if order != "asc" {
		order = "desc"
	}

	query := fmt.Sprintf(`
		SELECT id, tenant_id, session_id, trace_id, event_type, category, actor_id, payload_json, created_at
		FROM platform_audit_events %s ORDER BY created_at %s LIMIT $%d OFFSET $%d`,
		where, order, idx, idx+1)
	args = append(args, limit, offset)

	rows, err := pool.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := make([]auditRow, 0, limit)
	for rows.Next() {
		var r auditRow
		if err := rows.Scan(&r.ID, &r.TenantID, &r.SessionID, &r.TraceID,
			&r.EventType, &r.Category, &r.ActorID, &r.Payload, &r.CreatedAt); err == nil {
			out = append(out, r)
		}
	}
	return out, nil
}

// ── main ──────────────────────────────────────────────────────────────

func main() {
	resolvedBuf := newEventBuffer(100)
	auditBuf := newEventBuffer(100)

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

	// Batch flusher — flush every 5 s or when 200 events accumulate.
	flushSize := getenvInt("AUDIT_BATCH_SIZE", 200)
	flushInterval := time.Duration(getenvInt("AUDIT_FLUSH_INTERVAL_MS", 5000)) * time.Millisecond
	flusher := newAuditBatchFlusher(pool, flushSize, flushInterval)
	flusher.start()
	defer flusher.stop()

	instance := getenv("HOSTNAME", "local")

	// Subscribe: tool-permission resolved → audit
	if _, err := bus.Subscribe("audit-resolved-"+instance, eventbus.ToolPermissionResolvedSubject, func(env events.Envelope) {
		obs.IncEventReceived("audit-log-service", string(env.EventType))
		resolvedBuf.add(env)
		flusher.enqueue(env, "tool_permission")
	}); err != nil {
		log.Fatalf("subscribe resolved events: %v", err)
	}

	// Subscribe: security events → audit
	if _, err := bus.Subscribe("audit-security-"+instance, eventbus.AuditSecurityEventsSubject, func(env events.Envelope) {
		obs.IncEventReceived("audit-log-service", string(env.EventType))
		auditBuf.add(env)
		category := "security"
		if c, ok := env.Payload["category"].(string); ok && c != "" {
			category = c
		}
		flusher.enqueue(env, category)
	}); err != nil {
		log.Fatalf("subscribe audit events: %v", err)
	}

	// Sprint D: Rust core audit subscriptions — fanout-core, patch-merge-core,
	// memory-segment-core publish via async_nats (core NATS). We use SubscribeCore
	// (non-JetStream) to receive these. JetStream FANOUT/PATCH/MEMORY streams
	// capture the same messages for durable replay.
	if _, err := bus.SubscribeCore(eventbus.FanoutAuditSubject, func(env events.Envelope) {
		obs.IncEventReceived("audit-log-service", string(env.EventType))
		auditBuf.add(env)
		flusher.enqueue(env, "fanout")
	}); err != nil {
		log.Printf("audit-log-service: subscribe fanout.audit failed (non-fatal): %v", err)
	}
	if _, err := bus.SubscribeCore(eventbus.PatchAuditSubject, func(env events.Envelope) {
		obs.IncEventReceived("audit-log-service", string(env.EventType))
		auditBuf.add(env)
		flusher.enqueue(env, "patch")
	}); err != nil {
		log.Printf("audit-log-service: subscribe patch.audit failed (non-fatal): %v", err)
	}
	if _, err := bus.SubscribeCore(eventbus.MemoryAuditSubject, func(env events.Envelope) {
		obs.IncEventReceived("audit-log-service", string(env.EventType))
		auditBuf.add(env)
		flusher.enqueue(env, "memory")
	}); err != nil {
		log.Printf("audit-log-service: subscribe memory.audit failed (non-fatal): %v", err)
	}

	mux := http.NewServeMux()

	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})

	mux.HandleFunc("/healthz/readiness", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		if err := pool.Ping(ctx); err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			_ = json.NewEncoder(w).Encode(map[string]string{"status": "not_ready", "pg": err.Error()})
			return
		}
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(map[string]string{"status": "ready"})
	})

	mux.HandleFunc("/events/resolved", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"subject":      eventbus.ToolPermissionResolvedSubject,
			"buffer_count": resolvedBuf.len(),
			"events":       resolvedBuf.snapshot(),
		})
	})

	mux.HandleFunc("/events/audit", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"subject":      eventbus.AuditSecurityEventsSubject,
			"buffer_count": auditBuf.len(),
			"events":       auditBuf.snapshot(),
		})
	})

	// GET /events/persisted — filtered, paginated audit trail.
	mux.HandleFunc("/events/persisted", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		limit := clamp(getenvIntQ(q, "limit", 50), 1, 500)
		offset := max(getenvIntQ(q, "offset", 0), 0)
		order := q.Get("order")
		filters := map[string]string{
			"tenant_id": q.Get("tenant_id"), "session_id": q.Get("session_id"),
			"event_type": q.Get("event_type"), "category": q.Get("category"),
			"actor_id": q.Get("actor_id"), "since": q.Get("since"), "until": q.Get("until"),
		}

		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()
		rows, err := queryAudit(ctx, pool, filters, limit, offset, order)
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"count": len(rows), "limit": limit, "offset": offset, "events": rows,
		})
	})

	// GET /events/export — CSV export for compliance / offline analysis.
	mux.HandleFunc("/events/export", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		filters := map[string]string{
			"tenant_id": q.Get("tenant_id"),
			"since":     q.Get("since"),
			"until":     q.Get("until"),
		}

		ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
		defer cancel()
		rows, err := queryAudit(ctx, pool, filters, 10000, 0, "desc")
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}

		w.Header().Set("Content-Type", "text/csv; charset=utf-8")
		w.Header().Set("Content-Disposition", `attachment; filename="audit_export.csv"`)
		cw := csv.NewWriter(w)
		_ = cw.Write([]string{"id", "tenant_id", "session_id", "trace_id", "event_type", "category", "actor_id", "payload_json", "created_at"})
		for _, r := range rows {
			_ = cw.Write([]string{r.ID, r.TenantID, r.SessionID, r.TraceID, r.EventType, r.Category, r.ActorID, r.Payload, r.CreatedAt})
		}
		cw.Flush()
	})

	// GET /events/stats — audit summary.
	mux.HandleFunc("/events/stats", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()

		var total int
		_ = pool.QueryRow(ctx, `SELECT COUNT(*) FROM platform_audit_events`).Scan(&total)

		type catCount struct {
			Category string `json:"category"`
			Count    int    `json:"count"`
		}
		var cats []catCount
		cr, _ := pool.Query(ctx, `SELECT category, COUNT(*) AS cnt FROM platform_audit_events GROUP BY category ORDER BY cnt DESC LIMIT 20`)
		if cr != nil {
			defer cr.Close()
			for cr.Next() {
				var cc catCount
				if err := cr.Scan(&cc.Category, &cc.Count); err == nil {
					cats = append(cats, cc)
				}
			}
		}

		var oldest, newest string
		_ = pool.QueryRow(ctx, `SELECT COALESCE(MIN(created_at)::text,''), COALESCE(MAX(created_at)::text,'') FROM platform_audit_events`).Scan(&oldest, &newest)

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"total_events": total,
			"categories":   cats,
			"oldest_event": oldest,
			"newest_event": newest,
			"buffer_depth": atomic.LoadInt64(&lastBufferDepth),
		})
	})

	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		obs.MetricsHandler().ServeHTTP(w, r)
	})

	addr := getenv("AUDIT_ADDR", ":8087")
	log.Printf("audit-log-service listening on %s (batch_size=%d, flush_interval=%v)", addr, flushSize, flushInterval)
	handler := obs.Middleware("audit-log-service", mux)
	log.Fatal(http.ListenAndServe(addr, handler))
}

// ── utilities ──────────────────────────────────────────────────────────

func join(ss []string, sep string) string {
	if len(ss) == 0 {
		return ""
	}
	r := ss[0]
	for _, s := range ss[1:] {
		r += sep + s
	}
	return r
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getenvInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

func getenvIntQ(q interface{ Get(string) string }, key string, fallback int) int {
	if v := q.Get(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

func clamp(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
