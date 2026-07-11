package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"strconv"
	"strings"

	"github.com/agenthub/platform/shared/db"
	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/obs"
	"github.com/prometheus/client_golang/prometheus"
)

// ── Prometheus Metrics ─────────────────────────────────────────────

var (
	evalRunsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "eval_runs_total", Help: "Total evaluation runs by status."},
		[]string{"status"},
	)
	evalItemsScored = prometheus.NewCounter(
		prometheus.CounterOpts{Name: "eval_items_scored", Help: "Total golden items scored across all eval runs."},
	)
	evalRegressionDetected = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "eval_regression_detected", Help: "Regression alerts by dataset_id."},
		[]string{"dataset_id"},
	)
	evalRunDuration = prometheus.NewHistogram(
		prometheus.HistogramOpts{Name: "eval_run_duration_seconds", Help: "Duration of evaluation runs.", Buckets: []float64{1, 5, 10, 30, 60, 120, 300, 600}},
	)
)

func init() {
	obs.MustRegister(evalRunsTotal)
	obs.MustRegister(evalItemsScored)
	obs.MustRegister(evalRegressionDetected)
	obs.MustRegister(evalRunDuration)
}

// ── Data Types ──────────────────────────────────────────────────────

// GoldenDataset represents a golden dataset for offline evaluation.
type GoldenDataset struct {
	ID          string   `json:"id"`
	TenantID    string   `json:"tenant_id"`
	Name        string   `json:"name"`
	Description string   `json:"description"`
	Version     int      `json:"version"`
	ItemCount   int      `json:"item_count"`
	Tags        []string `json:"tags"`
	CreatedAt   string   `json:"created_at"`
	UpdatedAt   string   `json:"updated_at"`
}

// GoldenItem represents a single test case in a golden dataset.
type GoldenItem struct {
	ID                string                   `json:"id"`
	DatasetID         string                   `json:"dataset_id"`
	Query             string                   `json:"query"`
	ExpectedResponse  string                   `json:"expected_response"`
	ExpectedChunkIDs  []string                 `json:"expected_chunk_ids"`
	ExpectedToolCalls []map[string]interface{} `json:"expected_tool_calls"`
	Metadata          map[string]interface{}   `json:"metadata"`
	Index             int                      `json:"index"`
}

// EvalRun tracks a batch evaluation run against a golden dataset.
type EvalRun struct {
	ID          string                   `json:"id"`
	DatasetID   string                   `json:"dataset_id"`
	TenantID    string                   `json:"tenant_id"`
	Status      string                   `json:"status"`
	Config      map[string]interface{}   `json:"config"`
	Results     map[string]interface{}   `json:"results"`
	ItemResults []map[string]interface{} `json:"item_results"`
	StartedAt   string                   `json:"started_at,omitempty"`
	CompletedAt string                   `json:"completed_at,omitempty"`
	CreatedAt   string                   `json:"created_at"`
}

// ── Eval Handler ────────────────────────────────────────────────────

type evalHandler struct {
	pool *db.Pool
	bus  *eventbus.Client
}

func newEvalHandler(pool *db.Pool, bus *eventbus.Client) *evalHandler {
	return &evalHandler{pool: pool, bus: bus}
}

func (h *evalHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	rel := strings.TrimPrefix(r.URL.Path, "/platform/eval")
	rel = strings.TrimPrefix(rel, "/")

	// ── Runs ──
	if strings.HasPrefix(rel, "runs") {
		h.handleRuns(w, r, strings.TrimPrefix(rel, "runs"))
		return
	}

	// ── Datasets ──
	h.handleDatasets(w, r, rel)
}

// ── Dataset Routing ─────────────────────────────────────────────────

func (h *evalHandler) handleDatasets(w http.ResponseWriter, r *http.Request, rel string) {
	rel = strings.TrimPrefix(rel, "/")

	switch {
	case rel == "" && r.Method == http.MethodGet:
		h.listDatasets(w, r)
	case rel == "" && r.Method == http.MethodPost:
		h.createDataset(w, r)
	// POST /platform/eval/datasets/{id}/import
	case strings.HasSuffix(rel, "/import") && r.Method == http.MethodPost:
		id := strings.TrimSuffix(rel, "/import")
		h.importItems(w, r, id)
	// GET /platform/eval/datasets/{id}/export
	case strings.HasSuffix(rel, "/export") && r.Method == http.MethodGet:
		id := strings.TrimSuffix(rel, "/export")
		h.exportItems(w, r, id)
	// POST /platform/eval/datasets/{id}/items
	case strings.HasSuffix(rel, "/items") && r.Method == http.MethodPost:
		id := strings.TrimSuffix(rel, "/items")
		h.addItem(w, r, id)
	// PUT /platform/eval/datasets/{id}/items/{itemId}
	case strings.Contains(rel, "/items/") && r.Method == http.MethodPut:
		parts := strings.SplitN(rel, "/items/", 2)
		if len(parts) == 2 {
			h.updateItem(w, r, parts[0], parts[1])
		} else {
			http.Error(w, `{"error":"invalid path"}`, http.StatusBadRequest)
		}
	// DELETE /platform/eval/datasets/{id}/items/{itemId}
	case strings.Contains(rel, "/items/") && r.Method == http.MethodDelete:
		parts := strings.SplitN(rel, "/items/", 2)
		if len(parts) == 2 {
			h.deleteItem(w, r, parts[0], parts[1])
		} else {
			http.Error(w, `{"error":"invalid path"}`, http.StatusBadRequest)
		}
	// GET /platform/eval/datasets/{id}
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodGet:
		h.getDataset(w, r, rel)
	// PUT /platform/eval/datasets/{id}
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodPut:
		h.updateDataset(w, r, rel)
	// DELETE /platform/eval/datasets/{id}
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodDelete:
		h.deleteDataset(w, r, rel)
	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

// ── Run Routing ─────────────────────────────────────────────────────

func (h *evalHandler) handleRuns(w http.ResponseWriter, r *http.Request, rel string) {
	rel = strings.TrimPrefix(rel, "/")

	switch {
	case rel == "" && r.Method == http.MethodGet:
		h.listRuns(w, r)
	case rel == "" && r.Method == http.MethodPost:
		h.createRun(w, r)
	// POST /platform/eval/runs/{id}/cancel
	case strings.HasSuffix(rel, "/cancel") && r.Method == http.MethodPost:
		id := strings.TrimSuffix(rel, "/cancel")
		h.cancelRun(w, r, id)
	// GET /platform/eval/runs/{id}
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodGet:
		h.getRun(w, r, rel)
	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

// ── Dataset CRUD ────────────────────────────────────────────────────

func (h *evalHandler) listDatasets(w http.ResponseWriter, r *http.Request) {
	tenantID := r.URL.Query().Get("tenant_id")
	tag := r.URL.Query().Get("tag")

	query := `SELECT id, tenant_id, name, description, version, item_count, tags, created_at, updated_at
		 FROM platform_golden_datasets WHERE tenant_id = $1`
	args := []interface{}{tenantID}
	if tag != "" {
		query += ` AND $2 = ANY(tags)`
		args = append(args, tag)
	}
	query += ` ORDER BY updated_at DESC`

	rows, err := h.pool.Query(r.Context(), query, args...)
	if err != nil {
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	datasets := make([]GoldenDataset, 0)
	for rows.Next() {
		var d GoldenDataset
		if err := rows.Scan(&d.ID, &d.TenantID, &d.Name, &d.Description, &d.Version, &d.ItemCount, &d.Tags, &d.CreatedAt, &d.UpdatedAt); err != nil {
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
			return
		}
		datasets = append(datasets, d)
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"datasets": datasets, "count": len(datasets)})
}

func (h *evalHandler) createDataset(w http.ResponseWriter, r *http.Request) {
	var d GoldenDataset
	if err := json.NewDecoder(r.Body).Decode(&d); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if d.Name == "" {
		http.Error(w, `{"error":"name is required"}`, http.StatusBadRequest)
		return
	}
	if d.Tags == nil {
		d.Tags = []string{}
	}

	err := h.pool.QueryRow(r.Context(),
		`INSERT INTO platform_golden_datasets (tenant_id, name, description, version, tags)
		 VALUES ($1, $2, $3, $4, $5)
		 RETURNING id, tenant_id, name, description, version, item_count, tags, created_at, updated_at`,
		d.TenantID, d.Name, d.Description, d.Version, d.Tags,
	).Scan(&d.ID, &d.TenantID, &d.Name, &d.Description, &d.Version, &d.ItemCount, &d.Tags, &d.CreatedAt, &d.UpdatedAt)
	if err != nil {
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}

	log.Printf("eval dataset created: id=%s name=%s", d.ID, d.Name)
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(d)
}

func (h *evalHandler) getDataset(w http.ResponseWriter, r *http.Request, id string) {
	var d GoldenDataset
	err := h.pool.QueryRow(r.Context(),
		`SELECT id, tenant_id, name, description, version, item_count, tags, created_at, updated_at
		 FROM platform_golden_datasets WHERE id = $1`, id,
	).Scan(&d.ID, &d.TenantID, &d.Name, &d.Description, &d.Version, &d.ItemCount, &d.Tags, &d.CreatedAt, &d.UpdatedAt)
	if err != nil {
		http.Error(w, `{"error":"dataset not found"}`, http.StatusNotFound)
		return
	}

	// Load items
	rows, err := h.pool.Query(r.Context(),
		`SELECT id, dataset_id, query, expected_response, expected_chunk_ids, expected_tool_calls, metadata, index
		 FROM platform_golden_items WHERE dataset_id = $1 ORDER BY index`, id)
	if err != nil {
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	items := make([]GoldenItem, 0)
	for rows.Next() {
		var item GoldenItem
		if err := rows.Scan(&item.ID, &item.DatasetID, &item.Query, &item.ExpectedResponse, &item.ExpectedChunkIDs, &item.ExpectedToolCalls, &item.Metadata, &item.Index); err != nil {
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
			return
		}
		items = append(items, item)
	}

	_ = json.NewEncoder(w).Encode(map[string]interface{}{"dataset": d, "items": items})
}

func (h *evalHandler) updateDataset(w http.ResponseWriter, r *http.Request, id string) {
	var updates map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&updates); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	setClauses := make([]string, 0)
	args := make([]interface{}, 0)
	argIdx := 1

	if v, ok := updates["name"]; ok {
		setClauses = append(setClauses, "name = $"+strconv.Itoa(argIdx))
		args = append(args, v)
		argIdx++
	}
	if v, ok := updates["description"]; ok {
		setClauses = append(setClauses, "description = $"+strconv.Itoa(argIdx))
		args = append(args, v)
		argIdx++
	}
	if v, ok := updates["version"]; ok {
		setClauses = append(setClauses, "version = $"+strconv.Itoa(argIdx))
		args = append(args, v)
		argIdx++
	}
	if v, ok := updates["tags"]; ok {
		setClauses = append(setClauses, "tags = $"+strconv.Itoa(argIdx))
		args = append(args, v)
		argIdx++
	}

	if len(setClauses) == 0 {
		http.Error(w, `{"error":"no fields to update"}`, http.StatusBadRequest)
		return
	}

	args = append(args, id)
	query := "UPDATE platform_golden_datasets SET " + strings.Join(setClauses, ", ") + " WHERE id = $" + strconv.Itoa(argIdx)

	var d GoldenDataset
	err := h.pool.QueryRow(r.Context(),
		query+" RETURNING id, tenant_id, name, description, version, item_count, tags, created_at, updated_at",
		args...,
	).Scan(&d.ID, &d.TenantID, &d.Name, &d.Description, &d.Version, &d.ItemCount, &d.Tags, &d.CreatedAt, &d.UpdatedAt)
	if err != nil {
		http.Error(w, `{"error":"dataset not found"}`, http.StatusNotFound)
		return
	}

	log.Printf("eval dataset updated: id=%s", id)
	_ = json.NewEncoder(w).Encode(d)
}

func (h *evalHandler) deleteDataset(w http.ResponseWriter, r *http.Request, id string) {
	tag, err := h.pool.Exec(r.Context(), `DELETE FROM platform_golden_datasets WHERE id = $1`, id)
	if err != nil {
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	if tag.RowsAffected() == 0 {
		http.Error(w, `{"error":"dataset not found"}`, http.StatusNotFound)
		return
	}
	log.Printf("eval dataset deleted: id=%s", id)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
}

// ── Item CRUD ───────────────────────────────────────────────────────

func (h *evalHandler) addItem(w http.ResponseWriter, r *http.Request, datasetID string) {
	var item GoldenItem
	if err := json.NewDecoder(r.Body).Decode(&item); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if item.Query == "" {
		http.Error(w, `{"error":"query is required"}`, http.StatusBadRequest)
		return
	}
	if item.ExpectedToolCalls == nil {
		item.ExpectedToolCalls = []map[string]interface{}{}
	}
	if item.ExpectedChunkIDs == nil {
		item.ExpectedChunkIDs = []string{}
	}
	if item.Metadata == nil {
		item.Metadata = map[string]interface{}{}
	}

	// Auto-assign index
	var maxIdx int
	h.pool.QueryRow(r.Context(),
		`SELECT COALESCE(MAX(index), -1) FROM platform_golden_items WHERE dataset_id = $1`, datasetID,
	).Scan(&maxIdx)
	item.Index = maxIdx + 1

	err := h.pool.QueryRow(r.Context(),
		`INSERT INTO platform_golden_items (dataset_id, query, expected_response, expected_chunk_ids, expected_tool_calls, metadata, index)
		 VALUES ($1, $2, $3, $4, $5, $6, $7)
		 RETURNING id`,
		datasetID, item.Query, item.ExpectedResponse, item.ExpectedChunkIDs, item.ExpectedToolCalls, item.Metadata, item.Index,
	).Scan(&item.ID)
	if err != nil {
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	item.DatasetID = datasetID

	// Update item_count
	h.pool.Exec(r.Context(),
		`UPDATE platform_golden_datasets SET item_count = (
			SELECT COUNT(*) FROM platform_golden_items WHERE dataset_id = $1
		) WHERE id = $1`, datasetID)

	log.Printf("eval item added: dataset=%s item=%s", datasetID, item.ID)
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(item)
}

func (h *evalHandler) updateItem(w http.ResponseWriter, r *http.Request, datasetID, itemID string) {
	var item GoldenItem
	if err := json.NewDecoder(r.Body).Decode(&item); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	setClauses := make([]string, 0)
	args := make([]interface{}, 0)
	argIdx := 1

	if item.Query != "" {
		setClauses = append(setClauses, "query = $"+strconv.Itoa(argIdx))
		args = append(args, item.Query)
		argIdx++
	}
	if v, ok := interface{}(item.ExpectedResponse).(string); ok {
		setClauses = append(setClauses, "expected_response = $"+strconv.Itoa(argIdx))
		args = append(args, v)
		argIdx++
	}
	if item.ExpectedChunkIDs != nil {
		setClauses = append(setClauses, "expected_chunk_ids = $"+strconv.Itoa(argIdx))
		args = append(args, item.ExpectedChunkIDs)
		argIdx++
	}
	if item.ExpectedToolCalls != nil {
		setClauses = append(setClauses, "expected_tool_calls = $"+strconv.Itoa(argIdx))
		args = append(args, item.ExpectedToolCalls)
		argIdx++
	}
	if item.Metadata != nil {
		setClauses = append(setClauses, "metadata = $"+strconv.Itoa(argIdx))
		args = append(args, item.Metadata)
		argIdx++
	}
	if item.Index > 0 {
		setClauses = append(setClauses, "index = $"+strconv.Itoa(argIdx))
		args = append(args, item.Index)
		argIdx++
	}

	if len(setClauses) == 0 {
		http.Error(w, `{"error":"no fields to update"}`, http.StatusBadRequest)
		return
	}

	args = append(args, itemID, datasetID)
	query := "UPDATE platform_golden_items SET " + strings.Join(setClauses, ", ") +
		" WHERE id = $" + strconv.Itoa(argIdx) + " AND dataset_id = $" + strconv.Itoa(argIdx+1)

	var updated GoldenItem
	err := h.pool.QueryRow(r.Context(),
		query+" RETURNING id, dataset_id, query, expected_response, expected_chunk_ids, expected_tool_calls, metadata, index",
		args...,
	).Scan(&updated.ID, &updated.DatasetID, &updated.Query, &updated.ExpectedResponse,
		&updated.ExpectedChunkIDs, &updated.ExpectedToolCalls, &updated.Metadata, &updated.Index)
	if err != nil {
		http.Error(w, `{"error":"item not found"}`, http.StatusNotFound)
		return
	}

	log.Printf("eval item updated: dataset=%s item=%s", datasetID, itemID)
	_ = json.NewEncoder(w).Encode(updated)
}

func (h *evalHandler) deleteItem(w http.ResponseWriter, r *http.Request, datasetID, itemID string) {
	tag, err := h.pool.Exec(r.Context(),
		`DELETE FROM platform_golden_items WHERE id = $1 AND dataset_id = $2`, itemID, datasetID)
	if err != nil {
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	if tag.RowsAffected() == 0 {
		http.Error(w, `{"error":"item not found"}`, http.StatusNotFound)
		return
	}

	// Update item_count
	h.pool.Exec(r.Context(),
		`UPDATE platform_golden_datasets SET item_count = (
			SELECT COUNT(*) FROM platform_golden_items WHERE dataset_id = $1
		) WHERE id = $1`, datasetID)

	log.Printf("eval item deleted: dataset=%s item=%s", datasetID, itemID)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
}

// ── Import / Export ─────────────────────────────────────────────────

func (h *evalHandler) importItems(w http.ResponseWriter, r *http.Request, datasetID string) {
	var items []GoldenItem
	if err := json.NewDecoder(r.Body).Decode(&items); err != nil {
		http.Error(w, `{"error":"invalid json array"}`, http.StatusBadRequest)
		return
	}

	imported := 0
	for i, item := range items {
		if item.Query == "" {
			continue
		}
		if item.ExpectedToolCalls == nil {
			item.ExpectedToolCalls = []map[string]interface{}{}
		}
		if item.ExpectedChunkIDs == nil {
			item.ExpectedChunkIDs = []string{}
		}
		if item.Metadata == nil {
			item.Metadata = map[string]interface{}{}
		}
		if item.Index == 0 {
			item.Index = i + 1
		}
		_, err := h.pool.Exec(r.Context(),
			`INSERT INTO platform_golden_items (dataset_id, query, expected_response, expected_chunk_ids, expected_tool_calls, metadata, index)
			 VALUES ($1, $2, $3, $4, $5, $6, $7)`,
			datasetID, item.Query, item.ExpectedResponse, item.ExpectedChunkIDs, item.ExpectedToolCalls, item.Metadata, item.Index)
		if err != nil {
			log.Printf("import item error: %v", err)
			continue
		}
		imported++
	}

	// Update item_count
	h.pool.Exec(r.Context(),
		`UPDATE platform_golden_datasets SET item_count = (
			SELECT COUNT(*) FROM platform_golden_items WHERE dataset_id = $1
		) WHERE id = $1`, datasetID)

	log.Printf("eval items imported: dataset=%s imported=%d", datasetID, imported)
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"imported": imported, "total": len(items)})
}

func (h *evalHandler) exportItems(w http.ResponseWriter, r *http.Request, datasetID string) {
	rows, err := h.pool.Query(r.Context(),
		`SELECT id, dataset_id, query, expected_response, expected_chunk_ids, expected_tool_calls, metadata, index
		 FROM platform_golden_items WHERE dataset_id = $1 ORDER BY index`, datasetID)
	if err != nil {
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	items := make([]GoldenItem, 0)
	for rows.Next() {
		var item GoldenItem
		if err := rows.Scan(&item.ID, &item.DatasetID, &item.Query, &item.ExpectedResponse,
			&item.ExpectedChunkIDs, &item.ExpectedToolCalls, &item.Metadata, &item.Index); err != nil {
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
			return
		}
		items = append(items, item)
	}

	w.Header().Set("Content-Disposition", "attachment; filename=dataset-"+datasetID+".json")
	_ = json.NewEncoder(w).Encode(items)
}

// ── Evaluation Runs ─────────────────────────────────────────────────

func (h *evalHandler) listRuns(w http.ResponseWriter, r *http.Request) {
	datasetID := r.URL.Query().Get("dataset_id")
	status := r.URL.Query().Get("status")
	tenantID := r.URL.Query().Get("tenant_id")

	query := `SELECT id, dataset_id, tenant_id, status, config, results, item_results,
		COALESCE(started_at::text, ''), COALESCE(completed_at::text, ''), created_at
		FROM platform_eval_runs WHERE 1=1`
	args := make([]interface{}, 0)
	argIdx := 1

	if tenantID != "" {
		query += " AND tenant_id = $" + strconv.Itoa(argIdx)
		args = append(args, tenantID)
		argIdx++
	}
	if datasetID != "" {
		query += " AND dataset_id = $" + strconv.Itoa(argIdx)
		args = append(args, datasetID)
		argIdx++
	}
	if status != "" {
		query += " AND status = $" + strconv.Itoa(argIdx)
		args = append(args, status)
		argIdx++
	}
	query += " ORDER BY created_at DESC LIMIT 50"

	rows, err := h.pool.Query(r.Context(), query, args...)
	if err != nil {
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	runs := make([]EvalRun, 0)
	for rows.Next() {
		var run EvalRun
		if err := rows.Scan(&run.ID, &run.DatasetID, &run.TenantID, &run.Status,
			&run.Config, &run.Results, &run.ItemResults,
			&run.StartedAt, &run.CompletedAt, &run.CreatedAt); err != nil {
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
			return
		}
		runs = append(runs, run)
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"runs": runs, "count": len(runs)})
}

func (h *evalHandler) createRun(w http.ResponseWriter, r *http.Request) {
	var input struct {
		DatasetID string                 `json:"dataset_id"`
		TenantID  string                 `json:"tenant_id"`
		Config    map[string]interface{} `json:"config"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if input.DatasetID == "" {
		http.Error(w, `{"error":"dataset_id is required"}`, http.StatusBadRequest)
		return
	}
	if input.Config == nil {
		input.Config = map[string]interface{}{}
	}

	var run EvalRun
	err := h.pool.QueryRow(r.Context(),
		`INSERT INTO platform_eval_runs (dataset_id, tenant_id, status, config)
		 VALUES ($1, $2, 'pending', $3)
		 RETURNING id, dataset_id, tenant_id, status, config, results, item_results, created_at`,
		input.DatasetID, input.TenantID, input.Config,
	).Scan(&run.ID, &run.DatasetID, &run.TenantID, &run.Status, &run.Config, &run.Results, &run.ItemResults, &run.CreatedAt)
	if err != nil {
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}

	evalRunsTotal.WithLabelValues("pending").Inc()
	log.Printf("eval run created: id=%s dataset=%s", run.ID, run.DatasetID)

	// Trigger background execution
	go runEvaluation(context.Background(), &run)

	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(run)
}

func (h *evalHandler) getRun(w http.ResponseWriter, r *http.Request, id string) {
	var run EvalRun
	err := h.pool.QueryRow(r.Context(),
		`SELECT id, dataset_id, tenant_id, status, config, results, item_results,
			COALESCE(started_at::text, ''), COALESCE(completed_at::text, ''), created_at
		 FROM platform_eval_runs WHERE id = $1`, id,
	).Scan(&run.ID, &run.DatasetID, &run.TenantID, &run.Status,
		&run.Config, &run.Results, &run.ItemResults,
		&run.StartedAt, &run.CompletedAt, &run.CreatedAt)
	if err != nil {
		http.Error(w, `{"error":"run not found"}`, http.StatusNotFound)
		return
	}

	_ = json.NewEncoder(w).Encode(run)
}

func (h *evalHandler) cancelRun(w http.ResponseWriter, r *http.Request, id string) {
	tag, err := h.pool.Exec(r.Context(),
		`UPDATE platform_eval_runs SET status = 'cancelled', completed_at = now()
		 WHERE id = $1 AND status IN ('pending', 'running')`, id)
	if err != nil {
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	if tag.RowsAffected() == 0 {
		http.Error(w, `{"error":"run not found or already completed"}`, http.StatusNotFound)
		return
	}

	evalRunsTotal.WithLabelValues("cancelled").Inc()
	log.Printf("eval run cancelled: id=%s", id)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "cancelled"})
}

// ── Background evaluation runner (delegates to eval_runner.go) ──────

var runEvaluation func(ctx context.Context, run *EvalRun)
