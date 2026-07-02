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
	"github.com/agenthub/platform/shared/obs"
	"github.com/agenthub/platform/shared/state"
	"github.com/prometheus/client_golang/prometheus"
)

type DeliveryModes struct{ Modes []string `json:"modes"` }
type EventBuffer struct{ mu sync.RWMutex; items []events.Envelope; limit int }
type SessionEventIndex struct{ mu sync.RWMutex; items map[string][]events.Envelope; limit int }

// --- P2-8 SSE 背压与 chunk 合并指标 ---
var (
	sseConnections = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{Name: "sse_connections", Help: "Active SSE connections."},
		[]string{"session_id"},
	)
	sseWriteErrors = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "sse_write_errors_total", Help: "SSE write failures (slow client or timeout)."},
		[]string{"session_id"},
	)
	sseCoalescedChunks = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "sse_coalesced_chunks_total", Help: "Chunks coalesced into single SSE frames."},
		[]string{"session_id"},
	)
	sseFramesSent = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "sse_frames_sent_total", Help: "SSE frames sent (after coalescing)."},
		[]string{"session_id"},
	)
)

func init() {
	obs.MustRegister(sseConnections, sseWriteErrors, sseCoalescedChunks, sseFramesSent)
}

func NewEventBuffer(limit int) *EventBuffer { return &EventBuffer{limit: limit, items: make([]events.Envelope, 0, limit)} }
func NewSessionEventIndex(limit int) *SessionEventIndex { return &SessionEventIndex{items: make(map[string][]events.Envelope), limit: limit} }
func (b *EventBuffer) Add(e events.Envelope) { b.mu.Lock(); defer b.mu.Unlock(); if len(b.items) >= b.limit { b.items = append(b.items[1:], e); return }; b.items = append(b.items, e) }
func (b *EventBuffer) Snapshot() []events.Envelope { b.mu.RLock(); defer b.mu.RUnlock(); out := make([]events.Envelope, len(b.items)); copy(out, b.items); return out }
func (b *EventBuffer) Len() int { b.mu.RLock(); defer b.mu.RUnlock(); return len(b.items) }
func (s *SessionEventIndex) Add(e events.Envelope) { s.mu.Lock(); defer s.mu.Unlock(); list := append(s.items[e.SessionID], e); if len(list) > s.limit { list = list[len(list)-s.limit:] }; s.items[e.SessionID] = list }
func (s *SessionEventIndex) Replay(sessionID, afterCursor, eventType, status string, limit int) []events.Envelope {
	s.mu.RLock(); defer s.mu.RUnlock(); list := s.items[sessionID]; if len(list) == 0 { return []events.Envelope{} }
	start := 0
	if afterCursor != "" { for i, item := range list { if item.EventID == afterCursor || stringValue(item.Payload["stream_id"]) == afterCursor { start = i + 1; break } } }
	filtered := make([]events.Envelope, 0, len(list))
	for _, item := range list[start:] {
		if eventType != "" && string(item.EventType) != eventType { continue }
		if status != "" && stringValue(item.Payload["status"]) != status { continue }
		filtered = append(filtered, item)
		if limit > 0 && len(filtered) >= limit { break }
	}
	out := make([]events.Envelope, len(filtered)); copy(out, filtered); return out
}

func main() {
	modes := DeliveryModes{Modes: []string{"websocket", "sse", "summary_fallback"}}
	streamBuf, runtimeBuf := NewEventBuffer(200), NewEventBuffer(100)
	instance := getenv("HOSTNAME", "local")
	store := state.Connect(getenv("REDIS_ADDR", "127.0.0.1:6379"))
	defer func() { _ = store.Close() }()
	bus, err := eventbus.Connect(getenv("NATS_URL", "nats://127.0.0.1:4222")); if err != nil { log.Fatalf("connect event bus: %v", err) }; defer bus.Close()
	shutdown, errTr := obs.InitTracer(context.Background(), getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""), "stream-delivery-service"); if errTr != nil { log.Fatalf("init tracer: %v", errTr) }; defer shutdown(context.Background())
	if _, err := bus.Subscribe("stream-delivery-"+instance, eventbus.StreamEventsSubject, func(env events.Envelope) {
		obs.IncEventReceived("stream-delivery-service", string(env.EventType)); streamBuf.Add(env)
		if _, e := store.StreamAdd(context.Background(), env.TenantID, env.SessionID, env); e != nil { log.Printf("streamadd failed session=%s: %v", env.SessionID, e) }
		log.Printf("received stream event type=%s session=%s message=%s", env.EventType, env.SessionID, env.MessageID)

		// Sprint D: fanout stream events to fanout-core for channel broadcast.
		go func() {
			fanoutCtx, fanoutCancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer fanoutCancel()
			fanoutEnv := events.NewEnvelope(
				events.EventAgentReactTransition,
				env.TenantID,
				env.SessionID,
				env.TraceID,
				events.Producer{Service: "stream-delivery-service", Instance: instance},
				map[string]any{"channel": "stream", "source_event_id": env.EventID, "source_type": string(env.EventType)},
			)
			fanoutEnv.EventID = fmt.Sprintf("fanout-%s-%d", env.SessionID, time.Now().UnixMilli())
			fanoutEnv.Routing = &events.Routing{Channel: "stream", PartitionKey: env.SessionID, Priority: events.PriorityNormal}
			if pubErr := bus.PublishEnvelope(fanoutCtx, eventbus.FanoutEventsSubject, fanoutEnv); pubErr == nil {
				obs.IncEventPublished("stream-delivery-service", "fanout."+string(env.EventType))
			}
		}()
	}); err != nil { log.Fatalf("subscribe stream events: %v", err) }
	if _, err := bus.Subscribe("stream-delivery-runtime-"+instance, eventbus.AgentRuntimeResultsSubject, func(env events.Envelope) {
		obs.IncEventReceived("stream-delivery-service", string(env.EventType)); runtimeBuf.Add(env)
		if _, e := store.StreamAdd(context.Background(), env.TenantID, env.SessionID, env); e != nil { log.Printf("streamadd failed session=%s: %v", env.SessionID, e) }
		log.Printf("received runtime result type=%s session=%s message=%s", env.EventType, env.SessionID, env.MessageID)
	}); err != nil { log.Fatalf("subscribe runtime results: %v", err) }
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK); _, _ = w.Write([]byte("ok")) })
	mux.HandleFunc("/delivery/modes", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); _ = json.NewEncoder(w).Encode(modes) })
	mux.HandleFunc("/streams/recent", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); _ = json.NewEncoder(w).Encode(map[string]any{"subject": eventbus.StreamEventsSubject, "count": streamBuf.Len(), "events": streamBuf.Snapshot()}) })
	mux.HandleFunc("/runtime/results", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); _ = json.NewEncoder(w).Encode(map[string]any{"subject": eventbus.AgentRuntimeResultsSubject, "count": runtimeBuf.Len(), "events": runtimeBuf.Snapshot()}) })
	mux.HandleFunc("/streams/replay", func(w http.ResponseWriter, r *http.Request) {
		tenantID := r.URL.Query().Get("tenant_id"); sessionID := r.URL.Query().Get("session_id")
		if sessionID == "" { w.WriteHeader(http.StatusBadRequest); _ = json.NewEncoder(w).Encode(map[string]string{"error": "session_id is required"}); return }
		afterCursor, eventType, status, limit := r.URL.Query().Get("after_cursor"), r.URL.Query().Get("event_type"), r.URL.Query().Get("status"), parseLimit(r.URL.Query().Get("limit"), 50)
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second); defer cancel()
		entries, err := store.StreamRange(ctx, tenantID, sessionID, afterCursor, int64(limit))
		if err != nil { w.WriteHeader(http.StatusBadGateway); _ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()}); return }
		out := make([]events.Envelope, 0, len(entries))
		for _, e := range entries {
			var env events.Envelope
			if err := json.Unmarshal(e.Data, &env); err != nil { continue }
			if eventType != "" && string(env.EventType) != eventType { continue }
			if status != "" && stringValue(env.Payload["status"]) != status { continue }
			out = append(out, env)
		}
		var nextCursor string; if len(entries) > 0 { nextCursor = entries[len(entries)-1].ID }
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"session_id": sessionID, "after_cursor": afterCursor, "next_cursor": nextCursor, "event_type": eventType, "status": status, "count": len(out), "events": out})
	})
	mux.HandleFunc("/streams/timeline", func(w http.ResponseWriter, r *http.Request) {
		tenantID := r.URL.Query().Get("tenant_id"); sessionID := r.URL.Query().Get("session_id")
		if sessionID == "" { w.WriteHeader(http.StatusBadRequest); _ = json.NewEncoder(w).Encode(map[string]string{"error": "session_id is required"}); return }
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second); defer cancel()
		entries, err := store.StreamRange(ctx, tenantID, sessionID, "", 300)
		if err != nil { w.WriteHeader(http.StatusBadGateway); _ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()}); return }
		out := make([]events.Envelope, 0, len(entries))
		for _, e := range entries { var env events.Envelope; if json.Unmarshal(e.Data, &env) == nil { out = append(out, env) } }
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"session_id": sessionID, "count": len(out), "events": out})
	})
	// /streams/sse — SSE 端点（P2-8 增强：chunk 合并 + 写超时 + 慢客户端降级）
	//
	// 改进点（对照 capacity_model.json controls.stream_core：
	//   "chunk coalescing", "flush window", "slow-client summary mode"）：
	//   1. chunk 合并（coalescing）：一个 ticker 周期内取到的所有 entries 拼成
	//      一个 SSE 帧（单次 Write + Flush），减少 syscall 数量，提升吞吐。
	//   2. 写超时（flush window）：每次 Write 设 2s 写超时（ResponseController）。
	//      慢客户端导致写超时时，直接断开连接，避免 goroutine 堆积。
	//   3. 慢客户端降级：写失败计数到 sseWriteErrors 指标，连接断开。
	mux.HandleFunc("/streams/sse", func(w http.ResponseWriter, r *http.Request) {
		tenantID := r.URL.Query().Get("tenant_id"); sessionID := r.URL.Query().Get("session_id")
		if sessionID == "" { w.WriteHeader(http.StatusBadRequest); _ = json.NewEncoder(w).Encode(map[string]string{"error": "session_id is required"}); return }
		flusher, ok := w.(http.Flusher); if !ok { http.Error(w, "streaming unsupported", http.StatusInternalServerError); return }
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		// P2-8: 用 ResponseController 设置写超时，慢客户端写超时则断开。
		rc := http.NewResponseController(w)
		_ = rc.SetWriteDeadline(time.Now().Add(2 * time.Second))
		ctx := r.Context(); cursor := r.URL.Query().Get("after_cursor")
		ticker := time.NewTicker(700 * time.Millisecond); defer ticker.Stop()
		sseConnections.WithLabelValues(sessionID).Inc()
		defer sseConnections.WithLabelValues(sessionID).Dec()
		for {
			rctx, cancel := context.WithTimeout(ctx, 2*time.Second)
			entries, err := store.StreamRange(rctx, tenantID, sessionID, cursor, 20); cancel()
			if err == nil && len(entries) > 0 {
				// P2-8 chunk 合并：把本批所有 entries 拼到一个 strings.Builder，
				// 然后单次 Write + Flush，减少 syscall。
				var sb strings.Builder
				coalesced := 0
				for _, e := range entries {
					var env events.Envelope
					if json.Unmarshal(e.Data, &env) != nil { continue }
					// 每个 chunk 仍然是独立的 SSE event（id/event/data 三行），
					// 但合并到一次底层 Write 调用中。
					sb.WriteString(fmt.Sprintf("id: %s\nevent: %s\ndata: %s\n\n", env.EventID, env.EventType, e.Data))
					cursor = e.ID
					coalesced++
				}
				if coalesced > 0 {
					// 刷新写超时 deadline
					_ = rc.SetWriteDeadline(time.Now().Add(2 * time.Second))
					if _, werr := w.Write([]byte(sb.String())); werr != nil {
						// 慢客户端降级：写失败（超时或连接断开），直接终止
						sseWriteErrors.WithLabelValues(sessionID).Inc()
						log.Printf("sse write failed session=%s: %v (slow client, disconnecting)", sessionID, werr)
						return
					}
					flusher.Flush()
					if coalesced > 1 {
						sseCoalescedChunks.WithLabelValues(sessionID).Add(float64(coalesced - 1))
					}
					sseFramesSent.WithLabelValues(sessionID).Inc()
				}
			}
			select { case <-ctx.Done(): return; case <-ticker.C: }
		}
	})
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) { obs.MetricsHandler().ServeHTTP(w, r) })
	addr := getenv("DELIVERY_ADDR", ":8086"); log.Printf("stream-delivery-service listening on %s", addr); handler := obs.Middleware("stream-delivery-service", mux); log.Fatal(http.ListenAndServe(addr, handler))
}

func parseLimit(raw string, fallback int) int { if raw == "" { return fallback }; var parsed int; if _, err := fmt.Sscanf(raw, "%d", &parsed); err == nil && parsed > 0 { return parsed }; return fallback }
func stringValue(v any) string { if v == nil { return "" }; if s, ok := v.(string); ok { return s }; return fmt.Sprintf("%v", v) }
func getenv(key, fallback string) string { if v := os.Getenv(key); v != "" { return v }; return fallback }
func _trim(s string) string { return strings.TrimSpace(s) }
