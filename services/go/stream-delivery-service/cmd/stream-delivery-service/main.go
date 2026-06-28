package main

import (
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

type DeliveryModes struct{ Modes []string `json:"modes"` }
type EventBuffer struct{ mu sync.RWMutex; items []events.Envelope; limit int }
type SessionEventIndex struct{ mu sync.RWMutex; items map[string][]events.Envelope; limit int }

func NewEventBuffer(limit int) *EventBuffer { return &EventBuffer{limit: limit, items: make([]events.Envelope, 0, limit)} }
func NewSessionEventIndex(limit int) *SessionEventIndex { return &SessionEventIndex{items: make(map[string][]events.Envelope), limit: limit} }
func (b *EventBuffer) Add(e events.Envelope) { b.mu.Lock(); defer b.mu.Unlock(); if len(b.items) >= b.limit { b.items = append(b.items[1:], e); return }; b.items = append(b.items, e) }
func (b *EventBuffer) Snapshot() []events.Envelope { b.mu.RLock(); defer b.mu.RUnlock(); out := make([]events.Envelope, len(b.items)); copy(out, b.items); return out }
func (b *EventBuffer) Len() int { b.mu.RLock(); defer b.mu.RUnlock(); return len(b.items) }
func (s *SessionEventIndex) Add(e events.Envelope) { s.mu.Lock(); defer s.mu.Unlock(); list := append(s.items[e.SessionID], e); if len(list) > s.limit { list = list[len(list)-s.limit:] }; s.items[e.SessionID] = list }
func (s *SessionEventIndex) Replay(sessionID, afterCursor, eventType, status string, limit int) []events.Envelope {
	s.mu.RLock(); defer s.mu.RUnlock(); list := s.items[sessionID]; if len(list) == 0 { return []events.Envelope{} }
	start := 0
	if afterCursor != "" {
		for i, item := range list { if item.EventID == afterCursor || stringValue(item.Payload["stream_id"]) == afterCursor { start = i + 1; break } }
	}
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
	buffer, index := NewEventBuffer(200), NewSessionEventIndex(400)
	bus, err := eventbus.Connect(getenv("NATS_URL", "nats://127.0.0.1:4222")); if err != nil { log.Fatalf("connect event bus: %v", err) }; defer bus.Close()
	if _, err := bus.Subscribe(eventbus.StreamEventsSubject, func(env events.Envelope) { buffer.Add(env); index.Add(env); log.Printf("received stream event type=%s session=%s message=%s", env.EventType, env.SessionID, env.MessageID) }); err != nil { log.Fatalf("subscribe stream events: %v", err) }
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK); _, _ = w.Write([]byte("ok")) })
	mux.HandleFunc("/delivery/modes", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); _ = json.NewEncoder(w).Encode(modes) })
	mux.HandleFunc("/streams/recent", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "application/json"); _ = json.NewEncoder(w).Encode(map[string]any{"subject": eventbus.StreamEventsSubject, "count": buffer.Len(), "events": buffer.Snapshot()}) })
	mux.HandleFunc("/streams/replay", func(w http.ResponseWriter, r *http.Request) {
		sessionID := r.URL.Query().Get("session_id"); if sessionID == "" { w.WriteHeader(http.StatusBadRequest); _ = json.NewEncoder(w).Encode(map[string]string{"error": "session_id is required"}); return }
		afterCursor, eventType, status, limit := r.URL.Query().Get("after_cursor"), r.URL.Query().Get("event_type"), r.URL.Query().Get("status"), parseLimit(r.URL.Query().Get("limit"), 50)
		events := index.Replay(sessionID, afterCursor, eventType, status, limit)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"session_id": sessionID, "after_cursor": afterCursor, "event_type": eventType, "status": status, "count": len(events), "events": events})
	})
	mux.HandleFunc("/streams/timeline", func(w http.ResponseWriter, r *http.Request) {
		sessionID := r.URL.Query().Get("session_id"); if sessionID == "" { w.WriteHeader(http.StatusBadRequest); _ = json.NewEncoder(w).Encode(map[string]string{"error": "session_id is required"}); return }
		events := index.Replay(sessionID, "", "", "", 200)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"session_id": sessionID, "count": len(events), "events": events})
	})
	mux.HandleFunc("/streams/sse", func(w http.ResponseWriter, r *http.Request) {
		sessionID := r.URL.Query().Get("session_id"); if sessionID == "" { w.WriteHeader(http.StatusBadRequest); _ = json.NewEncoder(w).Encode(map[string]string{"error": "session_id is required"}); return }
		flusher, ok := w.(http.Flusher); if !ok { http.Error(w, "streaming unsupported", http.StatusInternalServerError); return }
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		ctx := r.Context(); cursor := r.URL.Query().Get("after_cursor")
		ticker := time.NewTicker(700 * time.Millisecond); defer ticker.Stop()
		for {
			batch := index.Replay(sessionID, cursor, "", "", 20)
			for _, env := range batch {
				payload, _ := json.Marshal(env)
				_, _ = fmt.Fprintf(w, "id: %s\nevent: %s\ndata: %s\n\n", env.EventID, env.EventType, payload)
				cursor = env.EventID
			}
			flusher.Flush()
			select { case <-ctx.Done(): return; case <-ticker.C: }
		}
	})
	addr := getenv("DELIVERY_ADDR", ":8086"); log.Printf("stream-delivery-service listening on %s", addr); log.Fatal(http.ListenAndServe(addr, mux))
}

func parseLimit(raw string, fallback int) int { if raw == "" { return fallback }; var parsed int; if _, err := fmt.Sscanf(raw, "%d", &parsed); err == nil && parsed > 0 { return parsed }; return fallback }
func stringValue(v any) string { if v == nil { return "" }; if s, ok := v.(string); ok { return s }; return fmt.Sprintf("%v", v) }
func getenv(key, fallback string) string { if v := os.Getenv(key); v != "" { return v }; return fallback }
func _trim(s string) string { return strings.TrimSpace(s) }
