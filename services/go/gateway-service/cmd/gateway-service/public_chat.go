package main

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
)

// handlePublicChat processes third-party API requests on /v1/public/chat.
// Authenticated via API Key (Bearer token), publishes to NATS, and returns
// a stream-capable response.
func handlePublicChat(bus *eventbus.Client, apiKeys *apiKeyHandler, w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "POST required"})
		return
	}

	// Extract and validate API key
	authHeader := r.Header.Get("Authorization")
	if !strings.HasPrefix(authHeader, "Bearer ") {
		w.WriteHeader(http.StatusUnauthorized)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "missing or invalid Authorization header"})
		return
	}
	providedKey := strings.TrimPrefix(authHeader, "Bearer ")

	// Validate key
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

	// Parse request body
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

	// Publish to NATS
	event := events.NewEnvelope(
		events.EventSessionMessageReceived,
		tenantID,
		sessionID,
		traceID,
		events.Producer{Service: "gateway-service", Instance: getenv("HOSTNAME", "local")},
		map[string]any{
			"content":  req.Message,
			"agent_id": req.AgentID,
			"source":   "public-api",
			"metadata": req.Metadata,
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

	// Update last used timestamp
	apiKeys.mu.Lock()
	if k, ok := apiKeys.keys[matchedKey.ID]; ok {
		k.LastUsedAt = time.Now().UTC().Format(time.RFC3339)
		apiKeys.keys[matchedKey.ID] = k
	}
	apiKeys.mu.Unlock()

	// Return response
	w.Header().Set("Content-Type", "application/json")
	if req.Stream {
		w.Header().Set("X-Stream-Hint", "SSE")
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"status":     "accepted",
		"session_id": sessionID,
		"trace_id":   traceID,
		"stream":     req.Stream,
	})
}
