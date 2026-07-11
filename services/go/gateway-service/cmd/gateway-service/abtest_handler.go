package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/agenthub/platform/shared/db"
	"github.com/prometheus/client_golang/prometheus"
)

// ── Prometheus metrics ─────────────────────────────────────────────────

var (
	abExperimentsActive = prometheus.NewGauge(
		prometheus.GaugeOpts{
			Name: "ab_experiments_active",
			Help: "Number of currently running A/B experiments.",
		},
	)
	abImpressionsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "ab_impressions_total",
			Help: "Total number of A/B impression recordings, by experiment and variant.",
		},
		[]string{"experiment_id", "variant_id"},
	)
	abWinnersTotal = prometheus.NewCounter(
		prometheus.CounterOpts{
			Name: "ab_winners_total",
			Help: "Number of completed A/B experiments that produced a clear winner.",
		},
	)
)

// ── Handler ────────────────────────────────────────────────────────────

type abtestHandler struct {
	pool *db.Pool
}

func newABTestHandler(pool *db.Pool) *abtestHandler {
	return &abtestHandler{pool: pool}
}

func (h *abtestHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	rel := strings.TrimPrefix(r.URL.Path, "/platform/ab-tests")
	rel = strings.TrimPrefix(rel, "/")

	switch {
	// GET /platform/ab-tests — list all
	case rel == "" && r.Method == http.MethodGet:
		h.list(w, r)
	// POST /platform/ab-tests — create
	case rel == "" && r.Method == http.MethodPost:
		h.create(w, r)
	// POST /platform/ab-tests/{id}/impression
	case strings.HasSuffix(rel, "/impression") && r.Method == http.MethodPost:
		id := strings.TrimSuffix(rel, "/impression")
		h.recordImpression(w, r, id)
	// POST /platform/ab-tests/{id}/start
	case strings.HasSuffix(rel, "/start") && r.Method == http.MethodPost:
		id := strings.TrimSuffix(rel, "/start")
		h.start(w, r, id)
	// POST /platform/ab-tests/{id}/pause
	case strings.HasSuffix(rel, "/pause") && r.Method == http.MethodPost:
		id := strings.TrimSuffix(rel, "/pause")
		h.pause(w, r, id)
	// POST /platform/ab-tests/{id}/complete
	case strings.HasSuffix(rel, "/complete") && r.Method == http.MethodPost:
		id := strings.TrimSuffix(rel, "/complete")
		h.complete(w, r, id)
	// GET /platform/ab-tests/{id}/results
	case strings.HasSuffix(rel, "/results") && r.Method == http.MethodGet:
		id := strings.TrimSuffix(rel, "/results")
		h.getResults(w, r, id)
	// GET /platform/ab-tests/{id}
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodGet:
		h.get(w, r, rel)
	// PUT /platform/ab-tests/{id}
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodPut:
		h.update(w, r, rel)
	// DELETE /platform/ab-tests/{id}
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodDelete:
		h.del(w, r, rel)
	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

// ── List ───────────────────────────────────────────────────────────────

func (h *abtestHandler) list(w http.ResponseWriter, r *http.Request) {
	if h.pool == nil {
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"experiments": []interface{}{}})
		return
	}

	tenantID := r.URL.Query().Get("tenant_id")
	status := r.URL.Query().Get("status")

	query := `SELECT id, tenant_id, name, description, agent_id, status, traffic_split,
	                  variants, metrics_config, created_at, started_at, ended_at
	           FROM platform_ab_experiments WHERE 1=1`
	args := []interface{}{}
	argIdx := 1

	if tenantID != "" {
		query += " AND tenant_id = $" + itoa(argIdx)
		args = append(args, tenantID)
		argIdx++
	}
	if status != "" {
		query += " AND status = $" + itoa(argIdx)
		args = append(args, status)
		argIdx++
	}
	query += " ORDER BY created_at DESC"

	rows, err := h.pool.Query(r.Context(), query, args...)
	if err != nil {
		log.Printf("abtest list error: %v", err)
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"experiments": []interface{}{}})
		return
	}
	defer rows.Close()

	experiments := make([]map[string]interface{}, 0)
	for rows.Next() {
		exp, err := h.scanExperiment(rows)
		if err != nil {
			log.Printf("abtest list scan error: %v", err)
			continue
		}
		// Attach live impression counts for quick summary.
		h.attachSummary(r.Context(), exp)
		experiments = append(experiments, exp)
	}

	_ = json.NewEncoder(w).Encode(map[string]interface{}{"experiments": experiments})
}

// ── Get single experiment ──────────────────────────────────────────────

func (h *abtestHandler) get(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	rows, err := h.pool.Query(r.Context(),
		`SELECT id, tenant_id, name, description, agent_id, status, traffic_split,
		        variants, metrics_config, created_at, started_at, ended_at
		 FROM platform_ab_experiments WHERE id=$1`, id)
	if err != nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	defer rows.Close()

	if !rows.Next() {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	exp, err := h.scanExperiment(rows)
	if err != nil {
		http.Error(w, `{"error":"scan error"}`, http.StatusInternalServerError)
		return
	}

	h.attachSummary(r.Context(), exp)
	_ = json.NewEncoder(w).Encode(exp)
}

// ── Create ─────────────────────────────────────────────────────────────

func (h *abtestHandler) create(w http.ResponseWriter, r *http.Request) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	var body map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	tenantID := getStr(body, "tenant_id")
	name := getStr(body, "name")
	if name == "" {
		http.Error(w, `{"error":"name is required"}`, http.StatusBadRequest)
		return
	}

	agentID := getStr(body, "agent_id")
	if agentID == "" {
		http.Error(w, `{"error":"agent_id is required"}`, http.StatusBadRequest)
		return
	}

	description := getStr(body, "description")
	status := getStr(body, "status")
	if status == "" {
		status = "draft"
	}
	trafficSplit := getInt(body, "traffic_split")
	if trafficSplit < 1 || trafficSplit > 99 {
		trafficSplit = 50
	}

	variants := body["variants"]
	variantsJSON, _ := json.Marshal(variants)
	if variants == nil || string(variantsJSON) == "null" {
		variantsJSON = []byte("[]")
	}

	metricsCfg := body["metrics_config"]
	metricsJSON, _ := json.Marshal(metricsCfg)
	if metricsCfg == nil || string(metricsJSON) == "null" {
		metricsJSON = []byte(`{"quality":0.4,"latency":0.2,"token_usage":0.15,"success_rate":0.15,"user_satisfaction":0.1}`)
	}

	var id string
	err := h.pool.QueryRow(r.Context(),
		`INSERT INTO platform_ab_experiments (tenant_id, name, description, agent_id, status, traffic_split, variants, metrics_config)
		 VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
		 RETURNING id`,
		tenantID, name, description, agentID, status, trafficSplit, string(variantsJSON), string(metricsJSON),
	).Scan(&id)
	if err != nil {
		log.Printf("abtest create error: %v", err)
		http.Error(w, `{"error":"create failed"}`, http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"id":            id,
		"tenant_id":     tenantID,
		"name":          name,
		"description":   description,
		"agent_id":      agentID,
		"status":        status,
		"traffic_split": trafficSplit,
		"variants":      variants,
		"metrics_config": metricsCfg,
		"created_at":    time.Now().UTC(),
	})
}

// ── Update ─────────────────────────────────────────────────────────────

func (h *abtestHandler) update(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	var body map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	// Only allow updating specific fields.
	sets := []string{}
	args := []interface{}{}
	argIdx := 1

	if v, ok := body["name"]; ok && v != nil {
		sets = append(sets, "name=$"+itoa(argIdx))
		args = append(args, v)
		argIdx++
	}
	if v, ok := body["description"]; ok && v != nil {
		sets = append(sets, "description=$"+itoa(argIdx))
		args = append(args, v)
		argIdx++
	}
	if _, ok := body["traffic_split"]; ok {
		ts := getInt(body, "traffic_split")
		if ts >= 1 && ts <= 99 {
			sets = append(sets, "traffic_split=$"+itoa(argIdx))
			args = append(args, ts)
			argIdx++
		}
	}
	if v, ok := body["variants"]; ok {
		b, _ := json.Marshal(v)
		sets = append(sets, "variants=$"+itoa(argIdx))
		args = append(args, string(b))
		argIdx++
	}
	if v, ok := body["metrics_config"]; ok {
		b, _ := json.Marshal(v)
		sets = append(sets, "metrics_config=$"+itoa(argIdx))
		args = append(args, string(b))
		argIdx++
	}

	if len(sets) == 0 {
		http.Error(w, `{"error":"no fields to update"}`, http.StatusBadRequest)
		return
	}

	args = append(args, id)
	query := "UPDATE platform_ab_experiments SET " + strings.Join(sets, ", ") + " WHERE id=$" + itoa(argIdx)
	_, err := h.pool.Exec(r.Context(), query, args...)
	if err != nil {
		log.Printf("abtest update error: %v", err)
		http.Error(w, `{"error":"update failed"}`, http.StatusInternalServerError)
		return
	}

	_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

// ── Delete ─────────────────────────────────────────────────────────────

func (h *abtestHandler) del(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	_, err := h.pool.Exec(r.Context(), "DELETE FROM platform_ab_experiments WHERE id=$1", id)
	if err != nil {
		log.Printf("abtest delete error: %v", err)
		http.Error(w, `{"error":"delete failed"}`, http.StatusInternalServerError)
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
}

// ── Start ──────────────────────────────────────────────────────────────

func (h *abtestHandler) start(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	_, err := h.pool.Exec(r.Context(),
		`UPDATE platform_ab_experiments SET status='running', started_at=now() WHERE id=$1`, id)
	if err != nil {
		log.Printf("abtest start error: %v", err)
		http.Error(w, `{"error":"start failed"}`, http.StatusInternalServerError)
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "running"})
}

// ── Pause ──────────────────────────────────────────────────────────────

func (h *abtestHandler) pause(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	_, err := h.pool.Exec(r.Context(),
		`UPDATE platform_ab_experiments SET status='paused' WHERE id=$1`, id)
	if err != nil {
		log.Printf("abtest pause error: %v", err)
		http.Error(w, `{"error":"pause failed"}`, http.StatusInternalServerError)
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "paused"})
}

// ── Complete + compute winner ──────────────────────────────────────────

func (h *abtestHandler) complete(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	result, err := computeWinner(r.Context(), h.pool, id)
	if err != nil {
		log.Printf("abtest complete error: %v", err)
		http.Error(w, `{"error":"complete failed: `+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}

	// Mark experiment as completed.
	_, err = h.pool.Exec(r.Context(),
		`UPDATE platform_ab_experiments SET status='completed', ended_at=now() WHERE id=$1`, id)
	if err != nil {
		log.Printf("abtest finalize error: %v", err)
	}

	// Record winner metric.
	if result.WinnerVariantID != "" {
		abWinnersTotal.Inc()
	}

	_ = json.NewEncoder(w).Encode(result)
}

// ── Record impression ──────────────────────────────────────────────────

func (h *abtestHandler) recordImpression(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	var body map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	variantID := getStr(body, "variant_id")
	if variantID == "" {
		http.Error(w, `{"error":"variant_id is required"}`, http.StatusBadRequest)
		return
	}
	sessionID := getStr(body, "session_id")
	if sessionID == "" {
		http.Error(w, `{"error":"session_id is required"}`, http.StatusBadRequest)
		return
	}

	metrics := body["metrics"]
	metricsJSON, _ := json.Marshal(metrics)
	if metrics == nil || string(metricsJSON) == "null" {
		metricsJSON = []byte("{}")
	}

	_, err := h.pool.Exec(r.Context(),
		`INSERT INTO platform_ab_impressions (experiment_id, variant_id, session_id, metrics)
		 VALUES ($1,$2,$3,$4)`,
		id, variantID, sessionID, string(metricsJSON))
	if err != nil {
		log.Printf("abtest impression error: %v", err)
		http.Error(w, `{"error":"record failed"}`, http.StatusInternalServerError)
		return
	}

	abImpressionsTotal.WithLabelValues(id, variantID).Inc()

	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "recorded"})
}

// ── Get results ────────────────────────────────────────────────────────

func (h *abtestHandler) getResults(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	rows, err := h.pool.Query(r.Context(),
		`SELECT experiment_id, winner_variant_id, confidence_level, p_value,
		        effect_size, variant_stats, test_method, computed_at
		 FROM platform_ab_results WHERE experiment_id=$1`, id)
	if err != nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	defer rows.Close()

	if !rows.Next() {
		http.Error(w, `{"error":"no results yet — complete the experiment first"}`, http.StatusNotFound)
		return
	}

	var expID, winnerVariantID, testMethod string
	var confidenceLevel, pValue, effectSize float64
	var variantStats, computedAt interface{}

	if err := rows.Scan(&expID, &winnerVariantID, &confidenceLevel, &pValue,
		&effectSize, &variantStats, &testMethod, &computedAt); err != nil {
		http.Error(w, `{"error":"scan error"}`, http.StatusInternalServerError)
		return
	}

	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"experiment_id":     expID,
		"winner_variant_id": winnerVariantID,
		"confidence_level":  confidenceLevel,
		"p_value":           pValue,
		"effect_size":       effectSize,
		"variant_stats":     variantStats,
		"test_method":       testMethod,
		"computed_at":       computedAt,
	})
}

// ── Helpers ────────────────────────────────────────────────────────────

func (h *abtestHandler) scanExperiment(rows interface{ Scan(dest ...interface{}) error }) (map[string]interface{}, error) {
	var id, tenantID, name, description, agentID, status string
	var trafficSplit int
	var variants, metricsConfig string
	var createdAt interface{}
	var startedAt, endedAt *time.Time

	if err := rows.Scan(&id, &tenantID, &name, &description, &agentID, &status,
		&trafficSplit, &variants, &metricsConfig, &createdAt, &startedAt, &endedAt); err != nil {
		return nil, err
	}

	var variantsJSON interface{}
	json.Unmarshal([]byte(variants), &variantsJSON)
	var metricsConfigJSON interface{}
	json.Unmarshal([]byte(metricsConfig), &metricsConfigJSON)

	exp := map[string]interface{}{
		"id":             id,
		"tenant_id":      tenantID,
		"name":           name,
		"description":    description,
		"agent_id":       agentID,
		"status":         status,
		"traffic_split":  trafficSplit,
		"variants":       variantsJSON,
		"metrics_config": metricsConfigJSON,
		"created_at":     createdAt,
	}

	if startedAt != nil {
		exp["started_at"] = *startedAt
	}
	if endedAt != nil {
		exp["ended_at"] = *endedAt
	}

	return exp, nil
}

func (h *abtestHandler) attachSummary(ctx context.Context, exp map[string]interface{}) {
	id := getStr(exp, "id")
	if h.pool == nil || id == "" {
		exp["total_impressions"] = 0
		return
	}

	var total int
	h.pool.QueryRow(ctx,
		`SELECT COUNT(*) FROM platform_ab_impressions WHERE experiment_id=$1`, id).Scan(&total)
	exp["total_impressions"] = total
}

// ── computeWinner orchestrator ─────────────────────────────────────────
//
// Loads impression metrics per variant, runs Welch's t-test on the primary
// quality metric, computes Cohen's d, and persists the result into
// platform_ab_results.

type ABTestResult struct {
	ExperimentID     string                   `json:"experiment_id"`
	WinnerVariantID  string                   `json:"winner_variant_id"`
	ConfidenceLevel  float64                  `json:"confidence_level"`
	PValue           float64                  `json:"p_value"`
	EffectSize       float64                  `json:"effect_size"`
	TestMethod       string                   `json:"test_method"`
	VariantStats     map[string]VariantStats  `json:"variant_stats"`
}

type VariantStats struct {
	Count            int     `json:"count"`
	MeanQuality      float64 `json:"mean_quality"`
	MeanLatencyMs    float64 `json:"mean_latency_ms"`
	MeanTokens       float64 `json:"mean_tokens"`
	SuccessRate      float64 `json:"success_rate"`
	MeanSatisfaction float64 `json:"mean_satisfaction"`
}

func computeWinner(ctx context.Context, pool *db.Pool, experimentID string) (*ABTestResult, error) {
	// 1. Load experiment to get variant IDs.
	var variantsJSON string
	err := pool.QueryRow(ctx,
		`SELECT variants FROM platform_ab_experiments WHERE id=$1`, experimentID).Scan(&variantsJSON)
	if err != nil {
		return nil, err
	}

	var variants []map[string]interface{}
	if err := json.Unmarshal([]byte(variantsJSON), &variants); err != nil {
		return nil, err
	}

	if len(variants) < 2 {
		return nil, err
	}

	// Collect variant IDs.
	variantIDs := make([]string, 0, len(variants))
	for _, v := range variants {
		if vid, ok := v["id"].(string); ok {
			variantIDs = append(variantIDs, vid)
		}
	}
	if len(variantIDs) < 2 {
		return nil, err
	}

	// 2. Load impression metrics for each variant.
	impressions := make(map[string][]map[string]interface{})
	for _, vid := range variantIDs {
		rows, err := pool.Query(ctx,
			`SELECT metrics FROM platform_ab_impressions
			 WHERE experiment_id=$1 AND variant_id=$2
			 ORDER BY created_at`, experimentID, vid)
		if err != nil {
			continue
		}
		var list []map[string]interface{}
		for rows.Next() {
			var mJSON string
			if err := rows.Scan(&mJSON); err != nil {
				continue
			}
			var m map[string]interface{}
			if err := json.Unmarshal([]byte(mJSON), &m); err != nil {
				continue
			}
			list = append(list, m)
		}
		rows.Close()
		impressions[vid] = list
	}

	// 3. Build per-variant stats.
	stats := make(map[string]VariantStats)
	for _, vid := range variantIDs {
		imps := impressions[vid]
		s := VariantStats{Count: len(imps)}
		if s.Count == 0 {
			stats[vid] = s
			continue
		}
		var totalQuality, totalLatency, totalTokens, totalSatisfaction float64
		successes := 0
		for _, imp := range imps {
			if q, ok := getFloatVal(imp, "quality"); ok {
				totalQuality += q
			}
			if l, ok := getFloatVal(imp, "latency_ms"); ok {
				totalLatency += l
			}
			if tk, ok := getFloatVal(imp, "tokens_used"); ok {
				totalTokens += tk
			}
			if suc, ok := imp["success"]; ok {
				if b, ok := suc.(bool); ok && b {
					successes++
				}
			}
			if sat, ok := getFloatVal(imp, "satisfaction"); ok {
				totalSatisfaction += sat
			}
		}
		n := float64(s.Count)
		s.MeanQuality = totalQuality / n
		s.MeanLatencyMs = totalLatency / n
		s.MeanTokens = totalTokens / n
		s.SuccessRate = float64(successes) / n
		s.MeanSatisfaction = totalSatisfaction / n
		stats[vid] = s
	}

	// 4. Run t-test on the primary quality metric.
	v0 := variantIDs[0]
	v1 := variantIDs[1]

	quals0 := extractMetric(impressions[v0], "quality")
	quals1 := extractMetric(impressions[v1], "quality")

	tStat, pValue, df := ttestWelch(quals0, quals1)

	// 5. Effect size.
	es := cohensD(quals0, quals1)

	// 6. Determine winner.
	winnerID := ""
	confidence := (1.0 - pValue) * 100.0
	if pValue < 0.05 && confidence > 0 {
		if stats[v0].MeanQuality > stats[v1].MeanQuality {
			winnerID = v0
		} else if stats[v1].MeanQuality > stats[v0].MeanQuality {
			winnerID = v1
		}
	}

	_ = tStat // used for logging/debugging
	_ = df    // used for logging/debugging

	// 7. Store results.
	statsJSON, _ := json.Marshal(stats)

	_, err = pool.Exec(ctx,
		`INSERT INTO platform_ab_results
		 (experiment_id, winner_variant_id, confidence_level, p_value, effect_size,
		  variant_stats, test_method, computed_at)
		 VALUES ($1,$2,$3,$4,$5,$6,'ttest',now())
		 ON CONFLICT (experiment_id) DO UPDATE SET
		   winner_variant_id=EXCLUDED.winner_variant_id,
		   confidence_level=EXCLUDED.confidence_level,
		   p_value=EXCLUDED.p_value,
		   effect_size=EXCLUDED.effect_size,
		   variant_stats=EXCLUDED.variant_stats,
		   test_method=EXCLUDED.test_method,
		   computed_at=now()`,
		experimentID, winnerID, confidence, pValue, es, string(statsJSON))
	if err != nil {
		return nil, err
	}

	return &ABTestResult{
		ExperimentID:    experimentID,
		WinnerVariantID: winnerID,
		ConfidenceLevel: confidence,
		PValue:          pValue,
		EffectSize:      es,
		TestMethod:      "ttest",
		VariantStats:    stats,
	}, nil
}

// extractMetric pulls a numeric metric key from impression data.
func extractMetric(imps []map[string]interface{}, key string) []float64 {
	values := make([]float64, 0, len(imps))
	for _, imp := range imps {
		if v, ok := getFloatVal(imp, key); ok {
			values = append(values, v)
		}
	}
	return values
}

// getFloatVal extracts a float64 value from a map, supporting both float64
// and json.Number types.
func getFloatVal(m map[string]interface{}, key string) (float64, bool) {
	v, ok := m[key]
	if !ok {
		return 0, false
	}
	switch n := v.(type) {
	case float64:
		return n, true
	case json.Number:
		f, err := n.Float64()
		if err != nil {
			return 0, false
		}
		return f, true
	default:
		return 0, false
	}
}

// Note: itoa is provided by agentnet_handler.go in the same package.
