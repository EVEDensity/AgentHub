// Sprint J4: Loki log query proxy — forwards log queries to Loki's HTTP API.
// Provides a secure gateway-managed endpoint for frontend log exploration.

package main

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"
)

// logsHandler proxies log queries to the Loki HTTP API. In development,
// it also serves a local in-memory log buffer so the UI works without Loki.
type logsHandler struct {
	lokiURL    string
	devMode    bool
	devLogs    []map[string]interface{}
	maxDevLogs int
}

func newLogsHandler() *logsHandler {
	return &logsHandler{
		lokiURL:    getenv("LOKI_URL", "http://localhost:3100"),
		devMode:    getenv("LOKI_URL", "") == "",
		devLogs:    make([]map[string]interface{}, 0),
		maxDevLogs: 500,
	}
}

func (h *logsHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}

	rel := strings.TrimPrefix(r.URL.Path, "/logs")
	rel = strings.TrimPrefix(rel, "/")

	switch {
	case r.Method == http.MethodPost && rel == "ingest":
		// POST /logs/ingest — ingest a log entry (used by services in dev mode)
		h.handleIngest(w, r)

	case r.Method == http.MethodGet && (rel == "query" || rel == ""):
		// GET /logs/query?service=X&level=Y&query=Z&limit=100
		h.handleQuery(w, r)

	default:
		http.NotFound(w, r)
	}
}

func (h *logsHandler) handleIngest(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	var entry map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&entry); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
		return
	}

	if entry["timestamp"] == nil {
		entry["timestamp"] = time.Now().UTC().Format(time.RFC3339Nano)
	}

	if h.devMode {
		h.devLogs = append(h.devLogs, entry)
		if len(h.devLogs) > h.maxDevLogs {
			h.devLogs = h.devLogs[len(h.devLogs)-h.maxDevLogs:]
		}
		json.NewEncoder(w).Encode(map[string]string{"status": "ingested", "mode": "dev"})
		return
	}

	// Forward to Loki push API
	resp, err := http.Post(h.lokiURL+"/loki/api/v1/push", "application/json", r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadGateway)
		json.NewEncoder(w).Encode(map[string]string{"error": "loki unreachable: " + err.Error()})
		return
	}
	defer resp.Body.Close()
	w.WriteHeader(resp.StatusCode)
	json.NewEncoder(w).Encode(map[string]string{"status": "ingested"})
}

func (h *logsHandler) handleQuery(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	// Dev mode: return in-memory logs with filtering
	if h.devMode {
		h.queryDevLogs(w, r)
		return
	}

	// Forward to Loki query_range API
	queryURL := h.lokiURL + "/loki/api/v1/query_range?" + r.URL.RawQuery
	resp, err := http.Get(queryURL)
	if err != nil {
		w.WriteHeader(http.StatusBadGateway)
		json.NewEncoder(w).Encode(map[string]string{"error": "loki unreachable: " + err.Error()})
		return
	}
	defer resp.Body.Close()

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	io.Copy(w, resp.Body)
}

func (h *logsHandler) queryDevLogs(w http.ResponseWriter, r *http.Request) {
	serviceFilter := r.URL.Query().Get("service")
	levelFilter := r.URL.Query().Get("level")
	queryFilter := r.URL.Query().Get("query")
	limit := 100

	var filtered []map[string]interface{}
	// Iterate newest first
	for i := len(h.devLogs) - 1; i >= 0; i-- {
		entry := h.devLogs[i]

		if serviceFilter != "" {
			if svc, ok := entry["service"].(string); !ok || svc != serviceFilter {
				continue
			}
		}
		if levelFilter != "" {
			if lvl, ok := entry["level"].(string); !ok || lvl != levelFilter {
				continue
			}
		}
		if queryFilter != "" {
			msg, _ := entry["message"].(string)
			if !strings.Contains(strings.ToLower(msg), strings.ToLower(queryFilter)) {
				continue
			}
		}

		filtered = append(filtered, entry)
		if len(filtered) >= limit {
			break
		}
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"mode":   "dev",
		"logs":   filtered,
		"total":  len(h.devLogs),
		"returned": len(filtered),
	})
}
