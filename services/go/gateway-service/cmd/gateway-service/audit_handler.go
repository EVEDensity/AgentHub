// Sprint J4: Audit log handler — provides gateway-level audit endpoints
// that proxy to the Python audit service or write directly to PostgreSQL.
// In dev mode, an in-memory ring buffer captures audit events so the UI
// works without a running Python/api service.
package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/db"
)

// AuditEntry is a single audit-log record visible to the frontend.
type AuditEntry struct {
	ID        string `json:"id"`
	TenantID  string `json:"tenant_id"`
	UserID    string `json:"user_id"`
	AgentID   string `json:"agent_id"`
	Action    string `json:"action"`
	RiskLevel string `json:"risk_level"`
	Decision  string `json:"decision"`
	Payload   string `json:"payload"`
	Timestamp string `json:"timestamp"`
}

// auditHandler provides REST endpoints for audit-log ingestion and querying.
// It proxies to the Python audit-service when configured; otherwise it uses a
// local in-memory buffer (dev mode).
type auditHandler struct {
	mu sync.RWMutex

	pool       *db.Pool
	pythonURL  string // e.g. "http://localhost:8000"
	devMode    bool
	devEntries []AuditEntry
	maxEntries int
}

func newAuditHandler(pool *db.Pool) *auditHandler {
	pythonURL := getenv("AUDIT_SERVICE_URL", "")
	return &auditHandler{
		pool:       pool,
		pythonURL:  pythonURL,
		devMode:    pythonURL == "",
		devEntries: make([]AuditEntry, 0),
		maxEntries: 1000,
	}
}

func (h *auditHandler) pg() bool { return h.pool != nil }

func (h *auditHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}

	rel := strings.TrimPrefix(r.URL.Path, "/audit")
	rel = strings.TrimPrefix(rel, "/")

	switch {
	case r.Method == http.MethodPost && rel == "entries":
		h.handleIngest(w, r)
	case r.Method == http.MethodGet && (rel == "logs" || rel == ""):
		h.handleQuery(w, r)
	case r.Method == http.MethodGet && strings.HasPrefix(rel, "logs/"):
		entryID := strings.TrimPrefix(rel, "logs/")
		h.handleDetail(w, r, entryID)
	case r.Method == http.MethodPost && rel == "sensitive-confirm":
		h.handleSensitiveConfirm(w, r)
	default:
		http.NotFound(w, r)
	}
}

// ── POST /audit/entries ─────────────────────────────────────────────────

func (h *auditHandler) handleIngest(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	var entry AuditEntry
	if err := json.NewDecoder(r.Body).Decode(&entry); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
		return
	}

	entry.ID = "audit-" + randomSuffix()
	if entry.Timestamp == "" {
		entry.Timestamp = time.Now().UTC().Format(time.RFC3339)
	}
	if entry.RiskLevel == "" {
		entry.RiskLevel = "normal"
	}

	// PG path — write directly to audit_log table
	if h.pg() {
		_, err := h.pool.Exec(r.Context(),
			`INSERT INTO audit_log (id, user_id, agent_id, action, risk_level, decision, payload_json, timestamp)
			 VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
			entry.ID, entry.UserID, entry.AgentID, entry.Action,
			entry.RiskLevel, entry.Decision, entry.Payload, entry.Timestamp)
		if err != nil {
			log.Printf("audit-handler: pg insert failed: %v", err)
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "pg insert failed"})
			return
		}
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(map[string]string{"status": "created", "audit_id": entry.ID})
		return
	}

	// Python proxy path
	if h.pythonURL != "" {
		body, _ := json.Marshal(entry)
		resp, err := http.Post(h.pythonURL+"/audit/entries", "application/json",
			strings.NewReader(string(body)))
		if err != nil {
			log.Printf("audit-handler: python proxy failed: %v", err)
		} else {
			resp.Body.Close()
		}
	}

	// Dev mode — in-memory ring buffer
	h.mu.Lock()
	h.devEntries = append(h.devEntries, entry)
	if len(h.devEntries) > h.maxEntries {
		h.devEntries = h.devEntries[len(h.devEntries)-h.maxEntries:]
	}
	h.mu.Unlock()

	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]string{"status": "created", "audit_id": entry.ID, "mode": "dev"})
}

// ── GET /audit/logs ─────────────────────────────────────────────────────

func (h *auditHandler) handleQuery(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	// PG path
	if h.pg() {
		h.queryAuditLogsPG(w, r)
		return
	}

	// Python proxy path
	if h.pythonURL != "" {
		resp, err := http.Get(h.pythonURL + "/audit/logs?" + r.URL.RawQuery)
		if err == nil {
			defer resp.Body.Close()
			w.WriteHeader(resp.StatusCode)
			// Stream the response
			buf := make([]byte, 4096)
			for {
				n, _ := resp.Body.Read(buf)
				if n == 0 {
					break
				}
				w.Write(buf[:n])
			}
			return
		}
		log.Printf("audit-handler: python proxy failed: %v", err)
	}

	// Dev mode fallback
	limit := 100
	actionFilter := r.URL.Query().Get("action")
	riskFilter := r.URL.Query().Get("risk_level")
	agentFilter := r.URL.Query().Get("agent_id")
	searchFilter := r.URL.Query().Get("search")

	h.mu.RLock()
	defer h.mu.RUnlock()

	var filtered []AuditEntry
	for i := len(h.devEntries) - 1; i >= 0; i-- {
		entry := h.devEntries[i]
		if actionFilter != "" && entry.Action != actionFilter {
			continue
		}
		if riskFilter != "" && entry.RiskLevel != riskFilter {
			continue
		}
		if agentFilter != "" && !strings.Contains(entry.AgentID, agentFilter) {
			continue
		}
		if searchFilter != "" {
			searchLower := strings.ToLower(searchFilter)
			if !strings.Contains(strings.ToLower(entry.Action), searchLower) &&
				!strings.Contains(strings.ToLower(entry.AgentID), searchLower) &&
				!strings.Contains(strings.ToLower(entry.Payload), searchLower) {
				continue
			}
		}
		filtered = append(filtered, entry)
		if len(filtered) >= limit {
			break
		}
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"mode":     "dev",
		"items":    filtered,
		"total":    len(h.devEntries),
		"returned": len(filtered),
	})
}

// ── GET /audit/logs/{id} ────────────────────────────────────────────────

func (h *auditHandler) handleDetail(w http.ResponseWriter, r *http.Request, entryID string) {
	w.Header().Set("Content-Type", "application/json")

	// PG path
	if h.pg() {
		var entry AuditEntry
		err := h.pool.QueryRow(r.Context(),
			`SELECT id, user_id, agent_id, action, risk_level, decision, payload_json, timestamp
			 FROM audit_log WHERE id=$1`, entryID).
			Scan(&entry.ID, &entry.UserID, &entry.AgentID, &entry.Action,
				&entry.RiskLevel, &entry.Decision, &entry.Payload, &entry.Timestamp)
		if err != nil {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "audit entry not found"})
			return
		}
		json.NewEncoder(w).Encode(entry)
		return
	}

	// Memory fallback
	h.mu.RLock()
	defer h.mu.RUnlock()
	for _, e := range h.devEntries {
		if e.ID == entryID {
			json.NewEncoder(w).Encode(e)
			return
		}
	}
	w.WriteHeader(http.StatusNotFound)
	json.NewEncoder(w).Encode(map[string]string{"error": "audit entry not found"})
}

// ── POST /audit/sensitive-confirm ───────────────────────────────────────

// SensitiveConfirmRequest represents a request to confirm a high/critical-risk tool.
type SensitiveConfirmRequest struct {
	TenantID  string `json:"tenant_id"`
	UserID    string `json:"user_id"`
	AgentID   string `json:"agent_id"`
	SessionID string `json:"session_id"`
	ToolName  string `json:"tool_name"`
	RiskLevel string `json:"risk_level"`
	Confirmed bool   `json:"confirmed"`
	Reason    string `json:"reason,omitempty"`
}

func (h *auditHandler) handleSensitiveConfirm(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	var req SensitiveConfirmRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
		return
	}
	if req.ToolName == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "tool_name is required"})
		return
	}

	decision := "denied"
	if req.Confirmed {
		decision = "confirmed"
	}

	// Sprint K1: emit sensitive tool confirmation metric for Prometheus alerting.
	sensitiveConfirm.WithLabelValues(req.RiskLevel, decision).Inc()

	// Record the confirmation as an audit entry
	entry := AuditEntry{
		ID:        "audit-" + randomSuffix(),
		TenantID:  req.TenantID,
		UserID:    req.UserID,
		AgentID:   req.AgentID,
		Action:    "tool.confirm",
		RiskLevel: req.RiskLevel,
		Decision:  decision,
		Payload:   mustMarshal(map[string]any{"tool_name": req.ToolName, "reason": req.Reason, "session_id": req.SessionID}),
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}

	if h.pg() {
		_, err := h.pool.Exec(context.Background(),
			`INSERT INTO audit_log (id, user_id, agent_id, action, risk_level, decision, payload_json, timestamp)
			 VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
			entry.ID, entry.UserID, entry.AgentID, entry.Action,
			entry.RiskLevel, entry.Decision, entry.Payload, entry.Timestamp)
		if err != nil {
			log.Printf("audit-handler: pg confirm insert failed: %v", err)
		}
	}

	// Also write to in-memory buffer for dev mode
	h.mu.Lock()
	h.devEntries = append(h.devEntries, entry)
	if len(h.devEntries) > h.maxEntries {
		h.devEntries = h.devEntries[len(h.devEntries)-h.maxEntries:]
	}
	h.mu.Unlock()

	status := http.StatusOK
	if !req.Confirmed {
		status = http.StatusForbidden
	}

	json.NewEncoder(w).Encode(map[string]any{
		"decision":  decision,
		"tool_name": req.ToolName,
		"audit_id":  entry.ID,
	})
	w.WriteHeader(status)
}

// ── PG query helper ─────────────────────────────────────────────────────

func (h *auditHandler) queryAuditLogsPG(w http.ResponseWriter, r *http.Request) {
	query := `SELECT id, COALESCE(user_id,''), COALESCE(agent_id,''), action, risk_level, decision, COALESCE(payload_json,''), timestamp FROM audit_log WHERE 1=1`
	args := []any{}
	argIdx := 1

	if f := r.URL.Query().Get("action"); f != "" {
		query += " AND action=$" + itoa(argIdx)
		args = append(args, f)
		argIdx++
	}
	if f := r.URL.Query().Get("risk_level"); f != "" {
		query += " AND risk_level=$" + itoa(argIdx)
		args = append(args, f)
		argIdx++
	}
	if f := r.URL.Query().Get("agent_id"); f != "" {
		query += " AND agent_id ILIKE $" + itoa(argIdx)
		args = append(args, "%"+f+"%")
		argIdx++
	}
	if f := r.URL.Query().Get("search"); f != "" {
		query += " AND (action ILIKE $" + itoa(argIdx) + " OR agent_id ILIKE $" + itoa(argIdx) + " OR payload_json ILIKE $" + itoa(argIdx) + ")"
		args = append(args, "%"+f+"%")
		argIdx++
	}

	query += " ORDER BY timestamp DESC LIMIT $" + itoa(argIdx)
	args = append(args, 200)
	argIdx++

	rows, err := h.pool.Query(r.Context(), query, args...)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "query failed: " + err.Error()})
		return
	}
	defer rows.Close()

	var entries []AuditEntry
	for rows.Next() {
		var e AuditEntry
		if err := rows.Scan(&e.ID, &e.UserID, &e.AgentID, &e.Action, &e.RiskLevel, &e.Decision, &e.Payload, &e.Timestamp); err == nil {
			entries = append(entries, e)
		}
	}
	json.NewEncoder(w).Encode(map[string]interface{}{
		"items": entries,
		"total": len(entries),
	})
}

// ── helpers ─────────────────────────────────────────────────────────────

func mustMarshal(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}
