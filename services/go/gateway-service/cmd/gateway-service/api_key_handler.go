package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
)

// apiKeyHandler serves /platform/api-keys CRUD.
// In-memory store for MVP; move to PostgreSQL in production.
type apiKeyHandler struct {
	mu   sync.RWMutex
	keys map[string]apiKeyRecord
}

type apiKeyRecord struct {
	ID        string   `json:"id"`
	Name      string   `json:"name"`
	KeyPrefix string   `json:"key_prefix"` // first 8 chars of the full key
	FullKey   string   `json:"full_key,omitempty"`
	Scopes    []string `json:"scopes"`
	RateLimit int      `json:"rate_limit"`
	Enabled   bool     `json:"enabled"`
	CreatedAt string   `json:"created_at"`
	LastUsedAt string  `json:"last_used_at,omitempty"`
}

func newAPIKeyHandler() *apiKeyHandler {
	return &apiKeyHandler{keys: make(map[string]apiKeyRecord)}
}

func (h *apiKeyHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	rel := strings.TrimPrefix(r.URL.Path, "/platform/api-keys")
	rel = strings.TrimPrefix(rel, "/")

	switch {
	case rel == "" && r.Method == http.MethodGet:
		h.list(w, r)
	case rel == "" && r.Method == http.MethodPost:
		h.create(w, r)
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodGet:
		h.get(w, r, rel)
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodDelete:
		h.revoke(w, r, rel)
	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

func (h *apiKeyHandler) list(w http.ResponseWriter, _ *http.Request) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	keys := make([]apiKeyRecord, 0, len(h.keys))
	for _, k := range h.keys {
		// Never expose full key in list
		clone := k
		clone.FullKey = ""
		keys = append(keys, clone)
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"keys": keys})
}

func (h *apiKeyHandler) create(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Name      string   `json:"name"`
		Scopes    []string `json:"scopes"`
		RateLimit int      `json:"rate_limit"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if req.Name == "" {
		http.Error(w, `{"error":"name is required"}`, http.StatusBadRequest)
		return
	}

	fullKey := "ah-" + generateAPIKey()
	prefix := fullKey[:11] // "ah-" + first 8 chars

	record := apiKeyRecord{
		ID:        "apikey-" + randomSuffix(),
		Name:      req.Name,
		KeyPrefix: prefix,
		FullKey:   fullKey,
		Scopes:    req.Scopes,
		RateLimit: req.RateLimit,
		Enabled:   true,
		CreatedAt: time.Now().UTC().Format(time.RFC3339),
	}
	if record.Scopes == nil {
		record.Scopes = []string{"chat"}
	}
	if record.RateLimit <= 0 {
		record.RateLimit = 60
	}

	h.mu.Lock()
	h.keys[record.ID] = record
	h.mu.Unlock()

	log.Printf("api-key created: id=%s name=%s prefix=%s scopes=%v", record.ID, record.Name, record.KeyPrefix, record.Scopes)
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(record)
}

func (h *apiKeyHandler) get(w http.ResponseWriter, _ *http.Request, id string) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	k, ok := h.keys[id]
	if !ok {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	clone := k
	clone.FullKey = ""
	_ = json.NewEncoder(w).Encode(clone)
}

func (h *apiKeyHandler) revoke(w http.ResponseWriter, _ *http.Request, id string) {
	h.mu.Lock()
	defer h.mu.Unlock()

	if _, ok := h.keys[id]; !ok {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	delete(h.keys, id)
	log.Printf("api-key revoked: id=%s", id)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "revoked"})
}

func generateAPIKey() string {
	b := make([]byte, 24)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}
