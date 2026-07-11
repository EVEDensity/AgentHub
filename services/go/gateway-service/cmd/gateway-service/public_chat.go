package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
)

// handlePublicChat processes third-party API requests on /v1/public/chat.
// Authenticated via API Key (Bearer token), publishes to NATS.
//
// Non-streaming (stream: false / omitted): returns a JSON acknowledgment
// immediately after publishing to NATS — best-effort fire-and-forget.
//
// Streaming (stream: true): after publishing, subscribes to NATS stream
// events for the session and relays them as SSE (text/event-stream) frames
// so the client receives incremental text chunks in real time.
func handlePublicChat(bus *eventbus.Client, apiKeys *apiKeyHandler, w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "POST required"})
		return
	}

	// ── Auth ────────────────────────────────────────────────────────
	authHeader := r.Header.Get("Authorization")
	if !strings.HasPrefix(authHeader, "Bearer ") {
		w.WriteHeader(http.StatusUnauthorized)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "missing or invalid Authorization header"})
		return
	}
	providedKey := strings.TrimPrefix(authHeader, "Bearer ")

	apiKeys.mu.RLock()
	var matchedKey *apiKeyRecord
	for _, k := range apiKeys.keys {
		if k.Enabled && k.FullKey == providedKey {
			clone := k
			matchedKey = &clone
			break
		}
	}
	apiKeys.mu.RUnlock()

	if matchedKey == nil {
		w.WriteHeader(http.StatusUnauthorized)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid or disabled API key"})
		return
	}

	// ── Parse body ──────────────────────────────────────────────────
	var req struct {
		Message   string         `json:"message"`
		SessionID string         `json:"session_id,omitempty"`
		AgentID   string         `json:"agent_id,omitempty"`
		Stream    bool           `json:"stream,omitempty"`
		Metadata  map[string]any `json:"metadata,omitempty"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid json body"})
		return
	}
	if req.Message == "" {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "message is required"})
		return
	}

	tenantID := "public-api"
	sessionID := req.SessionID
	if sessionID == "" {
		sessionID = "pub-" + randomSuffix()
	}
	traceID := "trace-pub-" + randomSuffix()

	// ── Publish to NATS ─────────────────────────────────────────────
	event := events.NewEnvelope(
		events.EventSessionMessageReceived,
		tenantID,
		sessionID,
		traceID,
		events.Producer{Service: "gateway-service", Instance: getenv("HOSTNAME", "local")},
		map[string]any{
			"content":    req.Message,
			"agent_id":   req.AgentID,
			"source":     "public-api",
			"metadata":   req.Metadata,
			"api_key_id": matchedKey.ID,
		},
	)
	event.EventID = "pub-evt-" + randomSuffix()
	event.Routing = &events.Routing{Channel: "public", PartitionKey: sessionID, Priority: events.PriorityNormal}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	if err := bus.PublishEnvelope(ctx, eventbus.SessionEventsSubject, event); err != nil {
		w.WriteHeader(http.StatusBadGateway)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}

	// ── Update last used timestamp ──────────────────────────────────
	apiKeys.mu.Lock()
	if k, ok := apiKeys.keys[matchedKey.ID]; ok {
		k.LastUsedAt = time.Now().UTC().Format(time.RFC3339)
		apiKeys.keys[matchedKey.ID] = k
	}
	apiKeys.mu.Unlock()

	// ── Non-streaming: return JSON ack immediately ─────────────────
	if !req.Stream {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"status":     "accepted",
			"session_id": sessionID,
			"trace_id":   traceID,
			"stream":     false,
		})
		return
	}

	// ── Streaming: subscribe NATS stream events → SSE relay ────────
	flusher, ok := w.(http.Flusher)
	if !ok {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "streaming not supported"})
		return
	}

	// SSE headers
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.WriteHeader(http.StatusOK)

	// Write initial connected event so the client knows the session ID
	fmt.Fprintf(w, "event: connected\ndata: {\"session_id\":\"%s\",\"status\":\"accepted\",\"trace_id\":\"%s\"}\n\n", sessionID, traceID)
	flusher.Flush()

	// ── Subscribe to stream events for this session ────────────────
	streamCh := make(chan events.Envelope, 64)
	sub, err := bus.SubscribeCore(eventbus.StreamEventsSubject, func(env events.Envelope) {
		if env.SessionID == sessionID {
			select {
			case streamCh <- env:
			default:
				// Drop if channel full (slow consumer)
			}
		}
	})
	if err != nil {
		log.Printf("public_chat: failed to subscribe core: %v", err)
		fmt.Fprintf(w, "event: error\ndata: {\"error\":\"failed to subscribe to stream\"}\n\n")
		flusher.Flush()
		return
	}
	defer sub.Unsubscribe()

	// ── Stream loop ─────────────────────────────────────────────────
	rc := http.NewResponseController(w)
	streamTimeout := 120 * time.Second
	timer := time.NewTimer(streamTimeout)
	defer timer.Stop()

	for {
		select {
		case <-r.Context().Done():
			// Client disconnected
			return

		case <-timer.C:
			// Stream timed out — send a timeout event and close
			fmt.Fprintf(w, "event: timeout\ndata: {\"message\":\"stream timeout after %v\"}\n\n", streamTimeout)
			flusher.Flush()
			return

		case env, ok := <-streamCh:
			if !ok {
				return
			}

			// Reset the timeout timer on each event
			if !timer.Stop() {
				select {
				case <-timer.C:
				default:
				}
			}
			timer.Reset(streamTimeout)

			// Marshal the event payload for the SSE data field
			data, _ := json.Marshal(env)
			fmt.Fprintf(w, "event: %s\ndata: %s\n\n", env.EventType, data)
			flusher.Flush()

			// Refresh write deadline
			_ = rc.SetWriteDeadline(time.Now().Add(30 * time.Second))

			// Close the stream on terminal events
			switch env.EventType {
			case events.EventSessionStreamComplete, events.EventSessionStreamError:
				return
			}
		}
	}
}
