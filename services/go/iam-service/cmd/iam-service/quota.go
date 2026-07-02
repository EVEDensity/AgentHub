package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/agenthub/platform/shared/db"
	"github.com/agenthub/platform/shared/obs"
	"github.com/prometheus/client_golang/prometheus"
)

// ── Quota metrics ──────────────────────────────────────────────────────

var (
	quotaExceeded = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "quota_exceeded_total", Help: "Quota exceeded events by tenant and resource."},
		[]string{"tenant_id", "resource"},
	)
	usageEvents = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "quota_usage_events_total", Help: "Usage events recorded."},
		[]string{"tenant_id", "event_type"},
	)
	billingCyclesCreated = prometheus.NewCounter(
		prometheus.CounterOpts{Name: "billing_cycles_created_total", Help: "Billing cycles created."},
	)
)

func init() {
	obs.MustRegister(quotaExceeded, usageEvents, billingCyclesCreated)
}

// ── Quota look-up ──────────────────────────────────────────────────────

// tenantQuota merges plan defaults with tenant-level overrides from
// platform_tenants.quotas_json. Overrides take precedence.
func tenantQuota(ctx context.Context, pool *db.Pool, tenantID string) (map[string]any, error) {
	var plan string
	var overrides string
	err := pool.QueryRow(ctx, `SELECT plan, quotas_json FROM platform_tenants WHERE id=$1`, tenantID).Scan(&plan, &overrides)
	if err != nil {
		return nil, err
	}
	// Load plan defaults.
	defs := map[string]any{}
	rows, err := pool.Query(ctx, `SELECT daily_tokens, monthly_tokens, max_sessions, max_agents, max_concurrent FROM platform_quota_definitions WHERE plan=$1`, plan)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	if rows.Next() {
		var dt, mt int64
		var ms, ma, mc int
		if err := rows.Scan(&dt, &mt, &ms, &ma, &mc); err == nil {
			defs["daily_tokens"] = dt
			defs["monthly_tokens"] = mt
			defs["max_sessions"] = ms
			defs["max_agents"] = ma
			defs["max_concurrent"] = mc
		}
	}

	// Merge overrides on top of defaults.
	var ov map[string]any
	if json.Unmarshal([]byte(overrides), &ov) == nil {
		for k, v := range ov {
			defs[k] = v
		}
	}
	defs["plan"] = plan
	return defs, nil
}

// currentUsage aggregates usage for a tenant in the current billing cycle
// (or the trailing 30 days if no cycle exists).
func currentUsage(ctx context.Context, pool *db.Pool, tenantID string) (map[string]any, error) {
	now := time.Now().UTC()
	// Find the open billing cycle, or default to now-30d.
	var cycleStart time.Time
	err := pool.QueryRow(ctx, `SELECT COALESCE(MIN(cycle_start), $1) FROM platform_billing_cycles WHERE tenant_id=$2 AND status='open'`,
		now.AddDate(0, 0, -30), tenantID).Scan(&cycleStart)
	if err != nil {
		return nil, err
	}

	usage := map[string]any{}
	var tokensToday, tokensMonth, sessionsTotal, agentsTotal int64
	dayStart := now.Truncate(24 * time.Hour)

	if err := pool.QueryRow(ctx, `SELECT COALESCE(SUM(amount), 0) FROM platform_usage_events WHERE tenant_id=$2 AND event_type='token_consumed' AND recorded_at >= $1`,
		dayStart, tenantID).Scan(&tokensToday); err == nil {
		usage["tokens_today"] = tokensToday
	}
	if err := pool.QueryRow(ctx, `SELECT COALESCE(SUM(amount), 0) FROM platform_usage_events WHERE tenant_id=$2 AND event_type='token_consumed' AND recorded_at >= $1`,
		cycleStart, tenantID).Scan(&tokensMonth); err == nil {
		usage["tokens_this_cycle"] = tokensMonth
	}
	if err := pool.QueryRow(ctx, `SELECT COUNT(*) FROM platform_usage_events WHERE tenant_id=$2 AND event_type='session_created' AND recorded_at >= $1`,
		cycleStart, tenantID).Scan(&sessionsTotal); err == nil {
		usage["sessions_this_cycle"] = sessionsTotal
	}
	if err := pool.QueryRow(ctx, `SELECT COALESCE(SUM(amount), 0) FROM platform_usage_events WHERE tenant_id=$2 AND event_type='agent_dispatched' AND recorded_at >= $1`,
		cycleStart, tenantID).Scan(&agentsTotal); err == nil {
		usage["agent_dispatches_this_cycle"] = agentsTotal
	}
	return usage, nil
}

// ── Quota enforcement ──────────────────────────────────────────────────

// QuotaCheckResult is the outcome of a quota check.
type QuotaCheckResult struct {
	Allowed     bool   `json:"allowed"`
	Resource    string `json:"resource"`
	Limit       int64  `json:"limit"`
	Current     int64  `json:"current"`
	Exceeded    bool   `json:"exceeded"`
	Message     string `json:"message,omitempty"`
	RetryAfter  string `json:"retry_after,omitempty"` // "in 4h" or similar
}

// checkQuota inspects whether a tenant has exceeded a given resource limit.
func checkQuota(ctx context.Context, pool *db.Pool, tenantID, resource string, amount int64) QuotaCheckResult {
	result := QuotaCheckResult{Allowed: true, Resource: resource}

	quota, err := tenantQuota(ctx, pool, tenantID)
	if err != nil {
		result.Allowed = true // fail-open on quota lookup errors
		return result
	}
	usage, err := currentUsage(ctx, pool, tenantID)
	if err != nil {
		result.Allowed = true
		return result
	}

	switch resource {
	case "daily_tokens":
		limit := int64Val(quota, "daily_tokens")
		current := int64Val(usage, "tokens_today") + amount
		result.Limit, result.Current = limit, current
		if limit > 0 && current > limit {
			result.Allowed = false
			result.Exceeded = true
			result.Message = fmt.Sprintf("daily token limit exceeded (%d/%d)", current, limit)
			result.RetryAfter = "next-day"
		}
	case "monthly_tokens":
		limit := int64Val(quota, "monthly_tokens")
		current := int64Val(usage, "tokens_this_cycle") + amount
		result.Limit, result.Current = limit, current
		if limit > 0 && current > limit {
			result.Allowed = false
			result.Exceeded = true
			result.Message = fmt.Sprintf("monthly token limit exceeded (%d/%d)", current, limit)
		}
	case "sessions":
		limit := int64Val(quota, "max_sessions")
		current := int64Val(usage, "sessions_this_cycle") + amount
		result.Limit, result.Current = limit, current
		if limit > 0 && current > limit {
			result.Allowed = false
			result.Exceeded = true
			result.Message = fmt.Sprintf("session limit exceeded (%d/%d)", current, limit)
		}
	case "agents":
		limit := int64Val(quota, "max_agents")
		current := int64Val(usage, "agent_dispatches_this_cycle") + amount
		result.Limit, result.Current = limit, current
		if limit > 0 && current > limit {
			result.Allowed = false
			result.Exceeded = true
			result.Message = fmt.Sprintf("agent dispatch limit exceeded (%d/%d)", current, limit)
		}
	}

	if result.Exceeded {
		quotaExceeded.WithLabelValues(tenantID, resource).Inc()
	}
	return result
}

// recordUsage writes a usage event to PG and updates the billing cycle totals.
func recordUsage(ctx context.Context, pool *db.Pool, tenantID, sessionID, eventType string, amount int64, meta map[string]any) {
	metaJSON, _ := json.Marshal(meta)
	id := fmt.Sprintf("usage-%s-%s-%d", tenantID, eventType, time.Now().UnixNano())
	_, _ = pool.Exec(ctx, `INSERT INTO platform_usage_events (id, tenant_id, session_id, event_type, amount, meta_json) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING`,
		id, tenantID, sessionID, eventType, amount, string(metaJSON))
	usageEvents.WithLabelValues(tenantID, eventType).Inc()
}

// ── HTTP handlers ──────────────────────────────────────────────────────

// serveQuotas handles /iam/quotas and /iam/quotas/{tenant_id}.
func serveQuotas(pool *db.Pool, w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()

	// Extract tenant_id from path.
	tenantFromPath := ""
	path := r.URL.Path
	if len(path) > len("/iam/quotas/") {
		tenantFromPath = path[len("/iam/quotas/"):]
	}

	switch {
	case tenantFromPath != "" && r.Method == http.MethodGet:
		// GET /iam/quotas/{tenant_id} — full quota + usage for one tenant.
		quota, err := tenantQuota(ctx, pool, tenantFromPath)
		if err != nil {
			jsonError(w, http.StatusNotFound, "tenant not found: "+tenantFromPath)
			return
		}
		usage, err := currentUsage(ctx, pool, tenantFromPath)
		if err != nil {
			usage = map[string]any{}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"tenant_id": tenantFromPath,
			"quota":     quota,
			"usage":     usage,
		})

	case r.Method == http.MethodGet:
		// GET /iam/quotas — list all plan definitions + tenant overrides.
		defRows, _ := pool.Query(ctx, `SELECT plan, daily_tokens, monthly_tokens, max_sessions, max_agents, max_concurrent FROM platform_quota_definitions ORDER BY plan`)
		plans := []map[string]any{}
		if defRows != nil {
			defer defRows.Close()
			for defRows.Next() {
				var p string
				var dt, mt int64
				var ms, ma, mc int
				if err := defRows.Scan(&p, &dt, &mt, &ms, &ma, &mc); err == nil {
					plans = append(plans, map[string]any{"plan": p, "daily_tokens": dt, "monthly_tokens": mt, "max_sessions": ms, "max_agents": ma, "max_concurrent": mc})
				}
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"plans": plans})

	case r.Method == http.MethodPost:
		// POST /iam/quotas — update plan definition (admin).
		var req struct {
			Plan           string `json:"plan"`
			DailyTokens    int64  `json:"daily_tokens"`
			MonthlyTokens  int64  `json:"monthly_tokens"`
			MaxSessions    int    `json:"max_sessions"`
			MaxAgents      int    `json:"max_agents"`
			MaxConcurrent  int    `json:"max_concurrent"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, http.StatusBadRequest, "invalid json body")
			return
		}
		if req.Plan == "" {
			jsonError(w, http.StatusBadRequest, "plan is required")
			return
		}
		_, err := pool.Exec(ctx, `INSERT INTO platform_quota_definitions (plan, daily_tokens, monthly_tokens, max_sessions, max_agents, max_concurrent) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (plan) DO UPDATE SET daily_tokens=EXCLUDED.daily_tokens, monthly_tokens=EXCLUDED.monthly_tokens, max_sessions=EXCLUDED.max_sessions, max_agents=EXCLUDED.max_agents, max_concurrent=EXCLUDED.max_concurrent`,
			req.Plan, req.DailyTokens, req.MonthlyTokens, req.MaxSessions, req.MaxAgents, req.MaxConcurrent)
		if err != nil {
			jsonError(w, http.StatusBadGateway, err.Error())
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "plan": req.Plan})

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// serveUsage handles /iam/usage and /iam/usage/{tenant_id}.
func serveUsage(pool *db.Pool, w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	switch r.Method {
	case http.MethodGet:
		tenantID := r.URL.Query().Get("tenant_id")
		limit := 50
		rows, err := pool.Query(ctx, `SELECT id, tenant_id, session_id, event_type, amount, meta_json, recorded_at FROM platform_usage_events WHERE ($1='' OR tenant_id=$1) ORDER BY recorded_at DESC LIMIT $2`, tenantID, limit)
		if err != nil {
			jsonError(w, http.StatusBadGateway, err.Error())
			return
		}
		defer rows.Close()
		out := []map[string]any{}
		for rows.Next() {
			var id, tid, sid, etype, meta, ts string
			var amt int64
			if err := rows.Scan(&id, &tid, &sid, &etype, &amt, &meta, &ts); err == nil {
				out = append(out, map[string]any{"id": id, "tenant_id": tid, "session_id": sid, "event_type": etype, "amount": amt, "meta_json": meta, "recorded_at": ts})
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"count": len(out), "events": out})

	case http.MethodPost:
		// POST /iam/usage — record a usage event.
		var req struct {
			TenantID  string         `json:"tenant_id"`
			SessionID string         `json:"session_id"`
			EventType string         `json:"event_type"`
			Amount    int64          `json:"amount"`
			Meta      map[string]any `json:"meta"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, http.StatusBadRequest, "invalid json body")
			return
		}
		if req.TenantID == "" || req.EventType == "" {
			jsonError(w, http.StatusBadRequest, "tenant_id and event_type are required")
			return
		}
		if req.Amount <= 0 {
			req.Amount = 1
		}
		recordUsage(ctx, pool, req.TenantID, req.SessionID, req.EventType, req.Amount, req.Meta)
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true})

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// serveQuotaCheck handles POST /iam/quotas/check — lightweight enforcement
// called by the gateway/realtime-orchestrator before LLM call dispatch.
func serveQuotaCheck(pool *db.Pool, w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")

	var req struct {
		TenantID string `json:"tenant_id"`
		Resource string `json:"resource"` // daily_tokens | monthly_tokens | sessions | agents
		Amount   int64  `json:"amount"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, http.StatusBadRequest, "invalid json body")
		return
	}
	if req.TenantID == "" {
		jsonError(w, http.StatusBadRequest, "tenant_id is required")
		return
	}
	if req.Resource == "" {
		req.Resource = "daily_tokens"
	}
	if req.Amount <= 0 {
		req.Amount = 1
	}

	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	result := checkQuota(ctx, pool, req.TenantID, req.Resource, req.Amount)
	if !result.Allowed {
		w.WriteHeader(http.StatusTooManyRequests)
	}
	_ = json.NewEncoder(w).Encode(result)
}

// serveBilling handles /iam/billing and /iam/billing/{tenant_id}.
func serveBilling(pool *db.Pool, w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	// Extract tenant_id from path: /iam/billing/{tenant_id} or /iam/billing/{tenant_id}/{action}
	path := r.URL.Path
	tenantFromPath := ""
	if len(path) > len("/iam/billing/") {
		rest := path[len("/iam/billing/"):]
		// strip trailing action
		parts := splitPath(rest)
		if len(parts) > 0 {
			tenantFromPath = parts[0]
		}
	}

	switch {
	case tenantFromPath != "" && pathHasAction(path, "/close") && r.Method == http.MethodPost:
		// POST /iam/billing/{tenant_id}/close — close current cycle and start new.
		now := time.Now().UTC()
		// Close current open cycle.
		_, _ = pool.Exec(ctx, `UPDATE platform_billing_cycles SET status='closed', closed_at=$1 WHERE tenant_id=$2 AND status='open'`, now, tenantFromPath)
		// Calculate totals from usage.
		var tokens int64
		var sessions int
		_ = pool.QueryRow(ctx, `SELECT COALESCE(SUM(amount),0) FROM platform_usage_events WHERE tenant_id=$2 AND event_type='token_consumed' AND recorded_at >= date_trunc('month',$1)`, now, tenantFromPath).Scan(&tokens)
		_ = pool.QueryRow(ctx, `SELECT COUNT(*) FROM platform_usage_events WHERE tenant_id=$2 AND event_type='session_created' AND recorded_at >= date_trunc('month',$1)`, now, tenantFromPath).Scan(&sessions)

		// Create next cycle.
		cycleStart := time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, time.UTC)
		nextCycle := cycleStart.AddDate(0, 1, 0)
		nextID := fmt.Sprintf("cycle-%s-%s", tenantFromPath, nextCycle.Format("200601"))
		_, _ = pool.Exec(ctx, `INSERT INTO platform_billing_cycles (id, tenant_id, cycle_start, cycle_end, status) VALUES ($1,$2,$3,$4,'open') ON CONFLICT (id) DO NOTHING`,
			nextID, tenantFromPath, cycleStart, nextCycle)
		billingCyclesCreated.Inc()

		_ = json.NewEncoder(w).Encode(map[string]any{
			"ok":               true,
			"tenant_id":        tenantFromPath,
			"previous_tokens":  tokens,
			"previous_sessions": sessions,
			"next_cycle_start": nextCycle.Format(time.RFC3339),
		})

	case tenantFromPath != "" && r.Method == http.MethodGet:
		// GET /iam/billing/{tenant_id} — billing summary.
		now := time.Now().UTC()
		cycleStart := now.AddDate(0, 0, -30)
		var cycID, status, invoice string
		var cycStart, cycEnd time.Time
		var tokTotal, sesTotal, agentTotal int64
		_ = pool.QueryRow(ctx, `SELECT id, cycle_start, cycle_end, status, total_tokens, total_sessions, total_agents, invoice_json FROM platform_billing_cycles WHERE tenant_id=$2 AND status='open' ORDER BY cycle_start DESC LIMIT 1`,
			tenantFromPath).Scan(&cycID, &cycStart, &cycEnd, &status, &tokTotal, &sesTotal, &agentTotal, &invoice)
		// Fall back to raw usage summation.
		if cycID == "" {
			_ = pool.QueryRow(ctx, `SELECT COALESCE(SUM(amount),0) FROM platform_usage_events WHERE tenant_id=$2 AND event_type='token_consumed' AND recorded_at>=$2`, cycleStart, tenantFromPath).Scan(&tokTotal)
			_ = pool.QueryRow(ctx, `SELECT COUNT(*) FROM platform_usage_events WHERE tenant_id=$2 AND event_type='session_created' AND recorded_at>=$2`, cycleStart, tenantFromPath).Scan(&sesTotal)
			_ = pool.QueryRow(ctx, `SELECT COALESCE(SUM(amount),0) FROM platform_usage_events WHERE tenant_id=$2 AND event_type='agent_dispatched' AND recorded_at>=$2`, cycleStart, tenantFromPath).Scan(&agentTotal)
		}

		quota, _ := tenantQuota(ctx, pool, tenantFromPath)
		usage, _ := currentUsage(ctx, pool, tenantFromPath)

		_ = json.NewEncoder(w).Encode(map[string]any{
			"tenant_id":    tenantFromPath,
			"cycle_id":     cycID,
			"cycle_start":  cycStart.Format(time.RFC3339),
			"cycle_end":    cycEnd.Format(time.RFC3339),
			"cycle_status": status,
			"totals":       map[string]any{"tokens": tokTotal, "sessions": sesTotal, "agents": agentTotal},
			"quota":        quota,
			"usage":        usage,
		})

	case r.Method == http.MethodGet:
		// GET /iam/billing — list all cycles.
		tenantID := r.URL.Query().Get("tenant_id")
		rows, err := pool.Query(ctx, `SELECT id, tenant_id, cycle_start, cycle_end, status, total_tokens, total_sessions, total_agents FROM platform_billing_cycles WHERE ($1='' OR tenant_id=$1) ORDER BY cycle_start DESC LIMIT 50`, tenantID)
		if err != nil {
			jsonError(w, http.StatusBadGateway, err.Error())
			return
		}
		defer rows.Close()
		cycles := []map[string]any{}
		for rows.Next() {
			var id, tid, status string
			var cs, ce time.Time
			var tok, ses, agent int64
			if err := rows.Scan(&id, &tid, &cs, &ce, &status, &tok, &ses, &agent); err == nil {
				cycles = append(cycles, map[string]any{"id": id, "tenant_id": tid, "cycle_start": cs.Format(time.RFC3339), "cycle_end": ce.Format(time.RFC3339), "status": status, "total_tokens": tok, "total_sessions": ses, "total_agents": agent})
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"count": len(cycles), "cycles": cycles})

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// ── helpers ────────────────────────────────────────────────────────────

func int64Val(m map[string]any, key string) int64 {
	if v, ok := m[key]; ok {
		switch vv := v.(type) {
		case float64:
			return int64(vv)
		case int64:
			return vv
		case int:
			return int64(vv)
		case json.Number:
			n, _ := vv.Int64()
			return n
		}
	}
	return 0
}

func splitPath(s string) []string {
	parts := []string{}
	current := ""
	for _, ch := range s {
		if ch == '/' {
			if current != "" {
				parts = append(parts, current)
				current = ""
			}
		} else {
			current += string(ch)
		}
	}
	if current != "" {
		parts = append(parts, current)
	}
	return parts
}

func pathHasAction(path, action string) bool {
	return len(path) >= len(action) && path[len(path)-len(action):] == action
}
