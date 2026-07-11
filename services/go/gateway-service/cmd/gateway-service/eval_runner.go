package main

import (
	"context"
	"encoding/json"
	"log"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/agenthub/platform/shared/db"
	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
)

// ── Eval Runner ─────────────────────────────────────────────────────

// EvalResults holds aggregate evaluation metrics.
type EvalResults struct {
	TotalItems    int                `json:"total_items"`
	Metrics       map[string]float64 `json:"metrics"`
	PerItem       []ItemScore        `json:"per_item"`
	RegressionDetected bool          `json:"regression_detected"`
	RegressionDetails  []RegrDetail  `json:"regression_details,omitempty"`
}

// ItemScore holds per-item evaluation results.
type ItemScore struct {
	ItemIndex       int                 `json:"item_index"`
	Query           string              `json:"query"`
	ActualResponse  string              `json:"actual_response"`
	ExpectedResponse string             `json:"expected_response"`
	Scores          map[string]float64  `json:"scores"`
	DurationMs      float64             `json:"duration_ms"`
	ToolCallsMatch  bool                `json:"tool_calls_match"`
}

// RegrDetail describes a specific regression.
type RegrDetail struct {
	Metric     string  `json:"metric"`
	Baseline   float64 `json:"baseline"`
	Current    float64 `json:"current"`
	ChangePct  float64 `json:"change_pct"`
}

// ComparisonResult holds baseline comparison data.
type ComparisonResult struct {
	HasBaseline        bool         `json:"has_baseline"`
	BaselineMetrics    map[string]float64 `json:"baseline_metrics"`
	CurrentMetrics     map[string]float64 `json:"current_metrics"`
	RegressionDetected bool         `json:"regression_detected"`
	DegradedMetrics    []RegrDetail `json:"degraded_metrics"`
}

// Runner wraps the eval runner with pool and bus references.
type Runner struct {
	pool *db.Pool
	bus  *eventbus.Client
}

func newRunner(pool *db.Pool, bus *eventbus.Client) *Runner {
	return &Runner{pool: pool, bus: bus}
}

// ── Top-level runEvaluation (called from eval_handler.go) ───────────

func (r *Runner) runEvaluation(ctx context.Context, run *EvalRun) {
	start := time.Now()
	evalRunsTotal.WithLabelValues("running").Inc()

	// Set status to running
	r.pool.Exec(ctx,
		`UPDATE platform_eval_runs SET status = 'running', started_at = now() WHERE id = $1`, run.ID)

	// Load dataset items
	items, err := r.loadItems(ctx, run.DatasetID)
	if err != nil {
		log.Printf("eval runner: load items failed: %v", err)
		r.failRun(ctx, run.ID, err.Error())
		evalRunsTotal.WithLabelValues("failed").Inc()
		return
	}

	if len(items) == 0 {
		log.Printf("eval runner: no items in dataset %s", run.DatasetID)
		r.completeRun(ctx, run.ID, EvalResults{TotalItems: 0, Metrics: map[string]float64{}})
		evalRunsTotal.WithLabelValues("completed").Inc()
		evalRunDuration.Observe(time.Since(start).Seconds())
		return
	}

	// Run evaluation on each item
	model := ""
	if v, ok := run.Config["model"]; ok {
		model = v.(string)
	}
	agentID := ""
	if v, ok := run.Config["agent_id"]; ok {
		agentID = v.(string)
	}

	var perItem []ItemScore
	for _, item := range items {
		itemScore := r.evalItem(ctx, item, model, agentID)
		perItem = append(perItem, itemScore)
		evalItemsScored.Inc()
	}

	// Compute aggregate metrics
	results := r.aggregateMetrics(perItem)

	// Compare with baseline
	comparison := r.compareWithBaseline(ctx, run.DatasetID, results.Metrics)
	if comparison.RegressionDetected {
		results.RegressionDetected = true
		results.RegressionDetails = comparison.DegradedMetrics
		evalRegressionDetected.WithLabelValues(run.DatasetID).Inc()

		// Publish regression alert
		r.publishAlert(ctx, run, comparison)
	}

	results.PerItem = perItem // Attach per-item for storage

	// Store results
	r.completeRun(ctx, run.ID, results)
	evalRunsTotal.WithLabelValues("completed").Inc()
	evalRunDuration.Observe(time.Since(start).Seconds())

	// Publish completion event
	r.publishComplete(ctx, run, results)
}

// ── Item Loading ────────────────────────────────────────────────────

func (r *Runner) loadItems(ctx context.Context, datasetID string) ([]GoldenItem, error) {
	rows, err := r.pool.Query(ctx,
		`SELECT id, dataset_id, query, expected_response, expected_chunk_ids, expected_tool_calls, metadata, index
		 FROM platform_golden_items WHERE dataset_id = $1 ORDER BY index`, datasetID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var items []GoldenItem
	for rows.Next() {
		var item GoldenItem
		if err := rows.Scan(&item.ID, &item.DatasetID, &item.Query, &item.ExpectedResponse,
			&item.ExpectedChunkIDs, &item.ExpectedToolCalls, &item.Metadata, &item.Index); err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, nil
}

// ── Per-Item Evaluation ─────────────────────────────────────────────

func (r *Runner) evalItem(ctx context.Context, item GoldenItem, model, agentID string) ItemScore {
	start := time.Now()

	// Build a simple request body for evaluation
	reqBody := map[string]interface{}{
		"model": model,
		"messages": []map[string]interface{}{
			{"role": "user", "content": item.Query},
		},
		"temperature": 0.0,
		"max_tokens":  256,
	}
	if agentID != "" {
		reqBody["agent_id"] = agentID
	}

	_ = reqBody // In production, this would call the model adapter via HTTP

	// Simulate response for now — in production this calls the actual model adapter
	simulatedResponse := "This is a simulated response for evaluation."
	simulatedToolCalls := []map[string]interface{}{}

	scores := map[string]float64{
		"exact_match":       r.computeExactMatch(simulatedResponse, item.ExpectedResponse),
		"fuzzy_match":       r.computeFuzzyMatch(simulatedResponse, item.ExpectedResponse),
		"tool_call_accuracy": r.computeToolCallAccuracy(simulatedToolCalls, item.ExpectedToolCalls),
		"latency_ms":        float64(time.Since(start).Milliseconds()),
	}

	return ItemScore{
		ItemIndex:        item.Index,
		Query:            item.Query,
		ActualResponse:   simulatedResponse,
		ExpectedResponse: item.ExpectedResponse,
		Scores:           scores,
		DurationMs:       scores["latency_ms"],
		ToolCallsMatch:   scores["tool_call_accuracy"] >= 1.0,
	}
}

// ── Metric Computation ──────────────────────────────────────────────

func (r *Runner) computeExactMatch(actual, expected string) float64 {
	if strings.TrimSpace(actual) == strings.TrimSpace(expected) {
		return 1.0
	}
	return 0.0
}

func (r *Runner) computeFuzzyMatch(actual, expected string) float64 {
	if expected == "" || actual == "" {
		if actual == expected {
			return 1.0
		}
		return 0.0
	}

	actualTokens := tokenize(actual)
	expectedTokens := tokenize(expected)

	if len(expectedTokens) == 0 {
		return 0.0
	}

	intersection := make(map[string]bool)
	for _, t := range expectedTokens {
		intersection[t] = true
	}

	matched := 0
	for _, t := range actualTokens {
		if intersection[t] {
			matched++
		}
	}

	precision := float64(matched) / float64(len(actualTokens))
	recall := float64(matched) / float64(len(expectedTokens))
	if precision+recall == 0 {
		return 0.0
	}
	return 2 * precision * recall / (precision + recall)
}

func tokenize(text string) []string {
	lower := strings.ToLower(text)
	// Simple whitespace + punctuation tokenization
	lower = strings.ReplaceAll(lower, ".", " ")
	lower = strings.ReplaceAll(lower, ",", " ")
	lower = strings.ReplaceAll(lower, "!", " ")
	lower = strings.ReplaceAll(lower, "?", " ")
	lower = strings.ReplaceAll(lower, ";", " ")
	lower = strings.ReplaceAll(lower, ":", " ")
	lower = strings.ReplaceAll(lower, "\n", " ")
	tokens := strings.Fields(lower)
	// Deduplicate for recall calculation but keep length for precision
	return tokens
}

func (r *Runner) computeToolCallAccuracy(actual, expected []map[string]interface{}) float64 {
	if len(expected) == 0 {
		return 1.0 // No tool calls expected means any behavior is acceptable
	}
	if len(actual) == 0 {
		return 0.0
	}

	matched := 0
	for _, exp := range expected {
		expName, _ := exp["name"].(string)
		for _, act := range actual {
			actName, _ := act["name"].(string)
			if strings.EqualFold(expName, actName) {
				matched++
				break
			}
		}
	}

	return float64(matched) / float64(len(expected))
}

// ── Aggregate Metrics ───────────────────────────────────────────────

func (r *Runner) aggregateMetrics(scores []ItemScore) EvalResults {
	if len(scores) == 0 {
		return EvalResults{}
	}

	// Collect values per metric
	metricValues := map[string][]float64{}
	for _, s := range scores {
		for k, v := range s.Scores {
			metricValues[k] = append(metricValues[k], v)
		}
	}

	aggregates := map[string]float64{}
	for metric, values := range metricValues {
		aggregates["mean_"+metric] = mean(values)
		aggregates["median_"+metric] = median(values)
		aggregates["stddev_"+metric] = stddev(values)
	}

	// Overall score: average of mean scores
	overallSum := 0.0
	overallCount := 0.0
	for k, v := range aggregates {
		if strings.HasPrefix(k, "mean_") && !strings.Contains(k, "latency") {
			overallSum += v
			overallCount++
		}
	}
	if overallCount > 0 {
		aggregates["overall_score"] = overallSum / overallCount
	}

	return EvalResults{
		TotalItems: len(scores),
		Metrics:    aggregates,
	}
}

// ── Baseline Comparison ─────────────────────────────────────────────

func (r *Runner) compareWithBaseline(ctx context.Context, datasetID string, currentMetrics map[string]float64) ComparisonResult {
	result := ComparisonResult{
		HasBaseline:     false,
		CurrentMetrics:  currentMetrics,
		DegradedMetrics: []RegrDetail{},
	}

	// Find the most recent completed run for this dataset (excluding current)
	var baselineResults map[string]interface{}
	err := r.pool.QueryRow(ctx,
		`SELECT results FROM platform_eval_runs
		 WHERE dataset_id = $1 AND status = 'completed'
		 ORDER BY completed_at DESC LIMIT 1`, datasetID,
	).Scan(&baselineResults)

	if err != nil || baselineResults == nil {
		return result
	}

	baselineMetrics, ok := baselineResults["metrics"].(map[string]interface{})
	if !ok {
		return result
	}

	result.HasBaseline = true
	result.BaselineMetrics = make(map[string]float64)
	for k, v := range baselineMetrics {
		if fv, ok := v.(float64); ok {
			result.BaselineMetrics[k] = fv
		}
	}

	// Compare key metrics: mean_exact_match, mean_fuzzy_match, mean_tool_call_accuracy
	keyMetrics := []string{"mean_exact_match", "mean_fuzzy_match", "mean_tool_call_accuracy", "overall_score"}
	degradationThreshold := 0.05 // 5%

	for _, metric := range keyMetrics {
		current, cOK := currentMetrics[metric]
		baseline, bOK := result.BaselineMetrics[metric]
		if !cOK || !bOK || baseline == 0 {
			continue
		}

		changePct := (current - baseline) / baseline
		if changePct < -degradationThreshold {
			result.RegressionDetected = true
			result.DegradedMetrics = append(result.DegradedMetrics, RegrDetail{
				Metric:    metric,
				Baseline:  baseline,
				Current:   current,
				ChangePct: changePct * 100,
			})
		}
	}

	return result
}

// ── Run State Updates ───────────────────────────────────────────────

func (r *Runner) completeRun(ctx context.Context, runID string, results EvalResults) {
	itemResultsJSON, _ := json.Marshal(results.PerItem)
	resultsJSON, _ := json.Marshal(EvalResults{
		TotalItems:        results.TotalItems,
		Metrics:           results.Metrics,
		RegressionDetected: results.RegressionDetected,
		RegressionDetails:  results.RegressionDetails,
	})

	r.pool.Exec(ctx,
		`UPDATE platform_eval_runs SET status = 'completed', completed_at = now(), results = $2, item_results = $3
		 WHERE id = $1`, runID, resultsJSON, itemResultsJSON)
}

func (r *Runner) failRun(ctx context.Context, runID, errMsg string) {
	errResults := map[string]interface{}{"error": errMsg}
	resultsJSON, _ := json.Marshal(errResults)
	r.pool.Exec(ctx,
		`UPDATE platform_eval_runs SET status = 'failed', completed_at = now(), results = $2
		 WHERE id = $1`, runID, resultsJSON)
}

// ── NATS Event Publishing ───────────────────────────────────────────

func (r *Runner) publishComplete(ctx context.Context, run *EvalRun, results EvalResults) {
	if r.bus == nil {
		return
	}

	producer := events.Producer{Service: "gateway-service", Instance: getenv("HOSTNAME", "local")}
	envelope := events.NewEnvelope(
		events.EventType("eval.run.completed"),
		run.TenantID,
		"",
		run.ID,
		producer,
		map[string]any{
			"run_id":             run.ID,
			"dataset_id":         run.DatasetID,
			"total_items":        results.TotalItems,
			"metrics":            results.Metrics,
			"regression_detected": results.RegressionDetected,
		},
	)
	envelope.EventID = "eval-" + run.ID

	if err := r.bus.PublishEnvelope(ctx, "agenthub.eval.run.completed", envelope); err != nil {
		log.Printf("eval runner: publish complete event failed: %v", err)
	}
}

func (r *Runner) publishAlert(ctx context.Context, run *EvalRun, comparison ComparisonResult) {
	if r.bus == nil {
		return
	}

	producer := events.Producer{Service: "gateway-service", Instance: getenv("HOSTNAME", "local")}
	envelope := events.NewEnvelope(
		events.EventType("eval.regression.detected"),
		run.TenantID,
		"",
		run.ID,
		producer,
		map[string]any{
			"run_id":           run.ID,
			"dataset_id":       run.DatasetID,
			"degraded_metrics": comparison.DegradedMetrics,
		},
	)
	envelope.EventID = "regression-" + run.ID

	if err := r.bus.PublishEnvelope(ctx, "agenthub.eval.regression.detected", envelope); err != nil {
		log.Printf("eval runner: publish regression alert failed: %v", err)
	}
}

// ── Math Helpers ────────────────────────────────────────────────────

func mean(values []float64) float64 {
	if len(values) == 0 {
		return 0.0
	}
	sum := 0.0
	for _, v := range values {
		sum += v
	}
	return sum / float64(len(values))
}

func median(values []float64) float64 {
	if len(values) == 0 {
		return 0.0
	}
	sorted := make([]float64, len(values))
	copy(sorted, values)
	sort.Float64s(sorted)
	mid := len(sorted) / 2
	if len(sorted)%2 == 0 {
		return (sorted[mid-1] + sorted[mid]) / 2.0
	}
	return sorted[mid]
}

func stddev(values []float64) float64 {
	if len(values) == 0 {
		return 0.0
	}
	avg := mean(values)
	sumSq := 0.0
	for _, v := range values {
		sumSq += (v - avg) * (v - avg)
	}
	return math.Sqrt(sumSq / float64(len(values)))
}
