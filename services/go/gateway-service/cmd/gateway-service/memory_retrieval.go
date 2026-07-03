package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"math"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
)

// ── Prometheus Metrics ────────────────────────────────────────────────

var (
	// contextDecayTotal counts decay calculation runs.
	contextDecayTotal = prometheus.NewCounter(
		prometheus.CounterOpts{
			Name: "context_decay_total",
			Help: "Total number of decay calculation runs executed on the segment store.",
		},
	)
	// memoryRetrievalLatency tracks retrieval request duration.
	memoryRetrievalLatency = prometheus.NewHistogram(
		prometheus.HistogramOpts{
			Name:    "memory_retrieval_latency_seconds",
			Help:    "Latency of memory retrieval requests in seconds.",
			Buckets: prometheus.DefBuckets,
		},
	)
)

func init() {
	prometheus.MustRegister(contextDecayTotal)
	prometheus.MustRegister(memoryRetrievalLatency)
}

// ── Decay Types ───────────────────────────────────────────────────────

// DecayConfig holds the parameters for time-based memory decay.
type DecayConfig struct {
	Lambda   float64 `json:"lambda"`    // decay rate constant
	HalfLife float64 `json:"half_life"` // half-life in days (ln(2)/lambda)
}

// DecayResultItem is per-segment decay calculation result.
type DecayResultItem struct {
	SegmentID string  `json:"segment_id"`
	AgeDays   float64 `json:"age_days"`
	OldWeight float64 `json:"old_weight"`
	NewWeight float64 `json:"new_weight"`
}

// DecayResponse is the response from a decay run.
type DecayResponse struct {
	Lambda     float64          `json:"lambda"`
	HalfLife   float64          `json:"half_life"`
	Segments   int              `json:"segments_processed"`
	Results    []DecayResultItem `json:"results"`
	TookMs     float64          `json:"took_ms"`
}

// ── Semantic Search Types ─────────────────────────────────────────────

// SemanticSearchRequest is the body for POST /context/memory/semantic-search.
type SemanticSearchRequest struct {
	Query    string  `json:"query"`
	TopK     int     `json:"top_k"`
	MinScore float64 `json:"min_score"`
}

// SemanticSearchItem is a single result from semantic search.
type SemanticSearchItem struct {
	SegmentID    string         `json:"segment_id"`
	Content      string         `json:"content"`
	Score        float64        `json:"score"`
	DecayWeight  float64        `json:"decay_weight"`
	SourceMetadata map[string]any `json:"source_metadata"`
}

// SemanticSearchResponse is the response for semantic search.
type SemanticSearchResponse struct {
	Query   string               `json:"query"`
	Results []SemanticSearchItem `json:"results"`
	TookMs  float64              `json:"took_ms"`
}

// ── Memory Retrieval Types ────────────────────────────────────────────

// MemoryRetrievalRequest is the body for POST /context/memory/retrieve.
type MemoryRetrievalRequest struct {
	AgentID       string   `json:"agent_id"`
	SessionID     string   `json:"session_id"`
	Query         string   `json:"query"`
	TopK          int      `json:"top_k"`
	MemoryTypes   []string `json:"memory_types"`
	TimeRangeHours int     `json:"time_range_hours"`
}

// MemoryRetrievalItem is a single ranked result from memory retrieval.
type MemoryRetrievalItem struct {
	Type      string  `json:"type"`      // episodic, semantic, procedural
	Content   string  `json:"content"`
	Score     float64 `json:"score"`
	Source    string  `json:"source"`
	Timestamp string  `json:"timestamp"`
}

// MemoryRetrievalResponse is the unified response for memory retrieval.
type MemoryRetrievalResponse struct {
	AgentID   string                `json:"agent_id"`
	SessionID string                `json:"session_id"`
	Query     string                `json:"query"`
	Results   []MemoryRetrievalItem `json:"results"`
	TotalHits int                   `json:"total_hits"`
	TookMs    float64               `json:"took_ms"`
}

// ── Conflict Resolution Types ─────────────────────────────────────────

// ConflictInfo represents an unresolved conflict.
type ConflictInfo struct {
	DecisionID      string  `json:"decision_id"`
	EntityID        string  `json:"entity_id"`
	Decision        string  `json:"decision"`
	SimilarityScore float64 `json:"similarity_score"`
	Reasoning       string  `json:"reasoning"`
	DecidedAt       string  `json:"decided_at"`
}

// ResolutionResult describes how a conflict was auto-resolved.
type ResolutionResult struct {
	EntityID        string `json:"entity_id"`
	Resolution      string `json:"resolution"`      // PREFER_NEWER, PREFER_LONGER, PREFER_HIGHER_CONFIDENCE, MANUAL
	Chosen          string `json:"chosen"`           // description of chosen entry
	Rejected        string `json:"rejected"`         // description of rejected entry
	Reasoning       string `json:"reasoning"`
}

// ── Embedding types for model-adapter communication ───────────────────

type embeddingRequest struct {
	Input []string `json:"input"`
	Model string   `json:"model"`
}

type embeddingResponse struct {
	Data []struct {
		Embedding []float64 `json:"embedding"`
		Index     int       `json:"index"`
	} `json:"data"`
}

// ── Decay Calculation ─────────────────────────────────────────────────

// defaultDecayConfig returns default decay parameters.
func defaultDecayConfig() DecayConfig {
	return DecayConfig{
		Lambda:   0.01,
		HalfLife: math.Log(2) / 0.01, // ~69.3 days
	}
}

// currentDecayConfig holds the runtime decay configuration.
var currentDecayConfig = defaultDecayConfig()
var decayConfigMu sync.RWMutex

// getDecayConfig returns the current decay configuration.
func (ce *contextEngine) getDecayConfig() DecayConfig {
	decayConfigMu.RLock()
	defer decayConfigMu.RUnlock()
	return currentDecayConfig
}

// setDecayConfig updates the decay configuration.
func (ce *contextEngine) setDecayConfig(cfg DecayConfig) {
	decayConfigMu.Lock()
	defer decayConfigMu.Unlock()
	currentDecayConfig = cfg
}

// computeDecayWeight calculates the decay weight for a given age in days.
func computeDecayWeight(ageDays, lambda float64) float64 {
	return math.Exp(-lambda * ageDays)
}

// computeHalfLife returns the half-life in days for a given lambda.
func computeHalfLife(lambda float64) float64 {
	if lambda <= 0 {
		return math.Inf(1)
	}
	return math.Log(2) / lambda
}

// ── Decay Endpoints ───────────────────────────────────────────────────

// handleDecay triggers decay calculation for all segments.
func (ce *contextEngine) handleDecay(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	cfg := ce.getDecayConfig()

	if ce.pgOrMem() {
		ce.handleDecayPG(w, r, cfg, start)
		return
	}
	ce.handleDecayMem(w, cfg, start)
}

func (ce *contextEngine) handleDecayMem(w http.ResponseWriter, cfg DecayConfig, start time.Time) {
	ce.mu.Lock()
	defer ce.mu.Unlock()

	now := time.Now().UTC()
	var results []DecayResultItem

	for id, seg := range ce.segments {
		createdAt, err := time.Parse(time.RFC3339, seg.CreatedAt)
		if err != nil {
			createdAt = now
		}
		ageDays := now.Sub(createdAt).Hours() / 24.0
		if ageDays < 0 {
			ageDays = 0
		}
		oldWeight := seg.DecayWeight
		if oldWeight == 0 {
			oldWeight = 1.0
		}
		newWeight := computeDecayWeight(ageDays, cfg.Lambda)
		seg.DecayWeight = newWeight
		seg.LastAccessed = now.Format(time.RFC3339)
		ce.segments[id] = seg

		results = append(results, DecayResultItem{
			SegmentID: id,
			AgeDays:   math.Round(ageDays*100) / 100,
			OldWeight: math.Round(oldWeight*10000) / 10000,
			NewWeight: math.Round(newWeight*10000) / 10000,
		})
	}

	contextDecayTotal.Inc()

	resp := DecayResponse{
		Lambda:   cfg.Lambda,
		HalfLife: cfg.HalfLife,
		Segments: len(results),
		Results:  results,
		TookMs:   float64(time.Since(start).Microseconds()) / 1000.0,
	}
	_ = json.NewEncoder(w).Encode(resp)
	log.Printf("context-engine: decay run completed segments=%d lambda=%.4f took=%.2fms",
		len(results), cfg.Lambda, resp.TookMs)
}

func (ce *contextEngine) handleDecayPG(w http.ResponseWriter, r *http.Request, cfg DecayConfig, start time.Time) {
	ctx := r.Context()
	now := time.Now().UTC()

	// Fetch all segments
	rows, err := ce.pool.Query(ctx,
		`SELECT id, created_at, metadata FROM platform_context_segments`)
	if err != nil {
		http.Error(w, `{"error":"failed to query segments"}`, http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type segRef struct {
		id        string
		createdAt time.Time
		metadata  []byte
	}
	segments := make([]segRef, 0)
	for rows.Next() {
		var s segRef
		var createdAt time.Time
		if err := rows.Scan(&s.id, &createdAt, &s.metadata); err != nil {
			continue
		}
		s.createdAt = createdAt
		segments = append(segments, s)
	}

	var results []DecayResultItem
	for _, s := range segments {
		ageDays := now.Sub(s.createdAt).Hours() / 24.0
		if ageDays < 0 {
			ageDays = 0
		}

		// Read old decay weight from metadata
		oldWeight := 1.0
		var meta map[string]any
		if len(s.metadata) > 0 {
			_ = json.Unmarshal(s.metadata, &meta)
		}
		if meta == nil {
			meta = map[string]any{}
		}
		if dw, ok := meta["decay_weight"].(float64); ok {
			oldWeight = dw
		}

		newWeight := computeDecayWeight(ageDays, cfg.Lambda)

		// Update metadata with new decay weight and last_accessed
		meta["decay_weight"] = newWeight
		meta["last_accessed"] = now.Format(time.RFC3339)
		metaJSON, _ := json.Marshal(meta)

		_, _ = ce.pool.Exec(ctx,
			`UPDATE platform_context_segments SET metadata=$1 WHERE id=$2`,
			metaJSON, s.id)

		results = append(results, DecayResultItem{
			SegmentID: s.id,
			AgeDays:   math.Round(ageDays*100) / 100,
			OldWeight: math.Round(oldWeight*10000) / 10000,
			NewWeight: math.Round(newWeight*10000) / 10000,
		})
	}

	contextDecayTotal.Inc()

	resp := DecayResponse{
		Lambda:   cfg.Lambda,
		HalfLife: cfg.HalfLife,
		Segments: len(results),
		Results:  results,
		TookMs:   float64(time.Since(start).Microseconds()) / 1000.0,
	}
	_ = json.NewEncoder(w).Encode(resp)
	log.Printf("context-engine: decay run (PG) completed segments=%d lambda=%.4f took=%.2fms",
		len(results), cfg.Lambda, resp.TookMs)
}

// handleDecayGetConfig returns the current decay configuration.
func (ce *contextEngine) handleDecayGetConfig(w http.ResponseWriter, r *http.Request) {
	cfg := ce.getDecayConfig()
	_ = json.NewEncoder(w).Encode(cfg)
}

// handleDecayPutConfig updates the decay configuration.
func (ce *contextEngine) handleDecayPutConfig(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Lambda float64 `json:"lambda"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if req.Lambda <= 0 {
		http.Error(w, `{"error":"lambda must be positive"}`, http.StatusBadRequest)
		return
	}

	newCfg := DecayConfig{
		Lambda:   req.Lambda,
		HalfLife: computeHalfLife(req.Lambda),
	}
	ce.setDecayConfig(newCfg)

	log.Printf("context-engine: decay config updated lambda=%.4f half_life=%.1f days",
		newCfg.Lambda, newCfg.HalfLife)
	_ = json.NewEncoder(w).Encode(newCfg)
}

// ── Last Accessed Tracking ────────────────────────────────────────────

// touchSegmentAccess updates last_accessed for a segment when it is retrieved.
func (ce *contextEngine) touchSegmentAccess(seg ContextSegment) {
	if ce.pgOrMem() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		now := time.Now().UTC()

		// Read current metadata, update last_accessed, write back
		var metaBytes []byte
		err := ce.pool.QueryRow(ctx,
			`SELECT metadata FROM platform_context_segments WHERE id=$1`, seg.ID).Scan(&metaBytes)
		if err != nil {
			return // segment may have been deleted
		}
		var meta map[string]any
		if len(metaBytes) > 0 {
			_ = json.Unmarshal(metaBytes, &meta)
		}
		if meta == nil {
			meta = map[string]any{}
		}
		meta["last_accessed"] = now.Format(time.RFC3339)
		metaJSON, _ := json.Marshal(meta)
		_, _ = ce.pool.Exec(ctx,
			`UPDATE platform_context_segments SET metadata=$1 WHERE id=$2`,
			metaJSON, seg.ID)
	} else {
		ce.mu.Lock()
		now := time.Now().UTC()
		if s, ok := ce.segments[seg.ID]; ok {
			s.LastAccessed = now.Format(time.RFC3339)
			ce.segments[seg.ID] = s
		}
		ce.mu.Unlock()
	}
}

// getDecayWeight extracts the decay weight from a segment's metadata or struct field.
func (ce *contextEngine) getDecayWeight(seg ContextSegment) float64 {
	if seg.DecayWeight > 0 {
		return seg.DecayWeight
	}
	if dw, ok := seg.Metadata["decay_weight"].(float64); ok {
		return dw
	}
	return 1.0
}

// ── Semantic Search ───────────────────────────────────────────────────

// handleSemanticSearch performs embedding-based search across L1 segments.
func (ce *contextEngine) handleSemanticSearch(w http.ResponseWriter, r *http.Request) {
	start := time.Now()

	var req SemanticSearchRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if req.Query == "" {
		http.Error(w, `{"error":"query is required"}`, http.StatusBadRequest)
		return
	}
	if req.TopK <= 0 {
		req.TopK = 10
	}
	if req.MinScore <= 0 {
		req.MinScore = 0.5
	}

	// Try embedding-based search first
	results := ce.semanticSearchEmbedding(r.Context(), req)
	if len(results) == 0 {
		// Fallback to keyword search
		results = ce.semanticSearchKeyword(req)
	}

	// Filter by min_score
	filtered := make([]SemanticSearchItem, 0)
	for _, item := range results {
		if item.Score >= req.MinScore {
			filtered = append(filtered, item)
		}
	}
	if filtered == nil {
		filtered = []SemanticSearchItem{}
	}

	// Truncate to top_k
	if len(filtered) > req.TopK {
		filtered = filtered[:req.TopK]
	}

	resp := SemanticSearchResponse{
		Query:   req.Query,
		Results: filtered,
		TookMs:  float64(time.Since(start).Microseconds()) / 1000.0,
	}
	_ = json.NewEncoder(w).Encode(resp)
}

// semanticSearchEmbedding calls the model-adapter /v1/embeddings endpoint.
func (ce *contextEngine) semanticSearchEmbedding(ctx context.Context, req SemanticSearchRequest) []SemanticSearchItem {
	modelAdapterURL := getenv("MODEL_ADAPTER_URL", "http://127.0.0.1:8001")

	// Get query embedding
	embReq := embeddingRequest{
		Input: []string{req.Query},
		Model: getenv("EMBEDDING_MODEL", "text-embedding-ada-002"),
	}
	bodyBytes, _ := json.Marshal(embReq)

	httpReq, err := http.NewRequestWithContext(ctx, "POST", modelAdapterURL+"/v1/embeddings", bytes.NewReader(bodyBytes))
	if err != nil {
		log.Printf("context-engine: embedding request build error: %v", err)
		return nil
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(httpReq)
	if err != nil {
		log.Printf("context-engine: embedding API error: %v", err)
		return nil
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		log.Printf("context-engine: embedding API returned status %d", resp.StatusCode)
		return nil
	}

	var embResp embeddingResponse
	if err := json.NewDecoder(resp.Body).Decode(&embResp); err != nil {
		log.Printf("context-engine: embedding decode error: %v", err)
		return nil
	}
	if len(embResp.Data) == 0 || len(embResp.Data[0].Embedding) == 0 {
		log.Printf("context-engine: empty embedding returned")
		return nil
	}
	queryVec := embResp.Data[0].Embedding

	// Get all segments and score by cosine similarity against stored embeddings or fallback
	var items []struct {
		item    SemanticSearchItem
		score   float64
		dw      float64
	}

	if ce.pgOrMem() {
		rows, err := ce.pool.Query(ctx,
			`SELECT id, content, metadata, created_at FROM platform_context_segments LIMIT 500`)
		if err != nil {
			return nil
		}
		defer rows.Close()

		for rows.Next() {
			var id, content string
			var metaBytes []byte
			var ct time.Time
			if err := rows.Scan(&id, &content, &metaBytes, &ct); err != nil {
				continue
			}

			var meta map[string]any
			if len(metaBytes) > 0 {
				_ = json.Unmarshal(metaBytes, &meta)
			}
			if meta == nil {
				meta = map[string]any{}
			}

			score := float64(0)
			if embData, ok := meta["embedding"]; ok {
				score = cosineSimilarity(queryVec, toFloat64Slice(embData))
			} else {
				// Fallback: keyword similarity
				score = keywordSimilarity(req.Query, content)
			}

			dw := 1.0
			if dwVal, ok := meta["decay_weight"].(float64); ok {
				dw = dwVal
			}
			effectiveScore := score * dw

			items = append(items, struct {
				item    SemanticSearchItem
				score   float64
				dw      float64
			}{
				item: SemanticSearchItem{
					SegmentID:      id,
					Content:        content,
					Score:          effectiveScore,
					DecayWeight:    dw,
					SourceMetadata: meta,
				},
				score: effectiveScore,
				dw:    dw,
			})
		}
	} else {
		ce.mu.RLock()
		for id, seg := range ce.segments {
			dw := ce.getDecayWeight(seg)
			score := float64(0)
			if embData, ok := seg.Metadata["embedding"]; ok {
				score = cosineSimilarity(queryVec, toFloat64Slice(embData))
			} else {
				score = keywordSimilarity(req.Query, seg.Content)
			}
			effectiveScore := score * dw

			items = append(items, struct {
				item    SemanticSearchItem
				score   float64
				dw      float64
			}{
				item: SemanticSearchItem{
					SegmentID:      id,
					Content:        seg.Content,
					Score:          effectiveScore,
					DecayWeight:    dw,
					SourceMetadata: seg.Metadata,
				},
				score: effectiveScore,
				dw:    dw,
			})
		}
		ce.mu.RUnlock()
	}

	sort.Slice(items, func(i, j int) bool {
		return items[i].score > items[j].score
	})

	results := make([]SemanticSearchItem, len(items))
	for i, it := range items {
		results[i] = it.item
	}
	return results
}

// semanticSearchKeyword is the fallback keyword-based search.
func (ce *contextEngine) semanticSearchKeyword(req SemanticSearchRequest) []SemanticSearchItem {
	qlower := strings.ToLower(req.Query)
	var items []struct {
		item  SemanticSearchItem
		score float64
	}

	if ce.pgOrMem() {
		// Use existing PG search as keyword fallback is already handled in searchPG
		// For simplicity, reuse the in-memory pattern
		ce.mu.RLock()
		defer ce.mu.RUnlock()
	} else {
		ce.mu.RLock()
		defer ce.mu.RUnlock()
	}

	for id, seg := range ce.segments {
		dw := ce.getDecayWeight(seg)
		score := keywordSimilarity(req.Query, seg.Content) * dw
		items = append(items, struct {
			item  SemanticSearchItem
			score float64
		}{
			item: SemanticSearchItem{
				SegmentID:      id,
				Content:        seg.Content,
				Score:          score,
				DecayWeight:    dw,
				SourceMetadata: seg.Metadata,
			},
			score: score,
		})
		_ = qlower // used in keywordSimilarity
	}

	sort.Slice(items, func(i, j int) bool {
		return items[i].score > items[j].score
	})

	results := make([]SemanticSearchItem, 0, len(items))
	for _, it := range items {
		results = append(results, it.item)
	}
	return results
}

// ── Memory Retrieval ──────────────────────────────────────────────────

// handleMemoryRetrieve performs multi-layer memory retrieval.
func (ce *contextEngine) handleMemoryRetrieve(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	defer func() {
		memoryRetrievalLatency.Observe(time.Since(start).Seconds())
	}()

	var req MemoryRetrievalRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if req.Query == "" {
		http.Error(w, `{"error":"query is required"}`, http.StatusBadRequest)
		return
	}
	if req.TopK <= 0 {
		req.TopK = 5
	}
	if req.TimeRangeHours <= 0 {
		req.TimeRangeHours = 168 // default 7 days
	}
	memoryTypes := req.MemoryTypes
	if len(memoryTypes) == 0 {
		memoryTypes = []string{"episodic", "semantic", "procedural"}
	}

	typeSet := make(map[string]bool)
	for _, t := range memoryTypes {
		typeSet[t] = true
	}

	var allItems []MemoryRetrievalItem
	seenHashes := make(map[string]bool)

	// L1: Episodic — recent segments from this session + decay-weighted keyword search
	if typeSet["episodic"] {
		episodicItems := ce.retrieveEpisodic(r.Context(), req)
		for _, item := range episodicItems {
			hash := contentHash(item.Content)
			if !seenHashes[hash] {
				seenHashes[hash] = true
				allItems = append(allItems, item)
			}
		}
	}

	// L2: Semantic — vector search via embedding or keyword fallback
	if typeSet["semantic"] {
		semReq := SemanticSearchRequest{
			Query:    req.Query,
			TopK:     req.TopK * 2,
			MinScore: 0.3,
		}
		semResults := ce.semanticSearchEmbedding(r.Context(), semReq)
		if len(semResults) == 0 {
			semResults = ce.semanticSearchKeyword(semReq)
		}
		for _, s := range semResults {
			hash := contentHash(s.Content)
			if !seenHashes[hash] {
				seenHashes[hash] = true
				allItems = append(allItems, MemoryRetrievalItem{
					Type:      "semantic",
					Content:   s.Content,
					Score:     s.Score,
					Source:    "L2-Qdrant/embedding",
					Timestamp: "",
				})
			}
		}
	}

	// L3: Procedural — entity-relation graph 1-hop around mentioned entities
	if typeSet["procedural"] {
		proceduralItems := ce.retrieveProcedural(req.Query)
		for _, item := range proceduralItems {
			hash := contentHash(item.Content)
			if !seenHashes[hash] {
				seenHashes[hash] = true
				allItems = append(allItems, item)
			}
		}
	}

	// Sort by score descending
	sort.Slice(allItems, func(i, j int) bool {
		return allItems[i].Score > allItems[j].Score
	})

	if allItems == nil {
		allItems = []MemoryRetrievalItem{}
	}

	// Truncate to top_k
	if len(allItems) > req.TopK {
		allItems = allItems[:req.TopK]
	}

	resp := MemoryRetrievalResponse{
		AgentID:   req.AgentID,
		SessionID: req.SessionID,
		Query:     req.Query,
		Results:   allItems,
		TotalHits: len(allItems),
		TookMs:    float64(time.Since(start).Microseconds()) / 1000.0,
	}
	_ = json.NewEncoder(w).Encode(resp)
}

// retrieveEpisodic fetches recent segments for a session with decay-weighted keyword scoring.
func (ce *contextEngine) retrieveEpisodic(ctx context.Context, req MemoryRetrievalRequest) []MemoryRetrievalItem {
	qlower := strings.ToLower(req.Query)
	cutoff := time.Now().UTC().Add(-time.Duration(req.TimeRangeHours) * time.Hour)

	var items []MemoryRetrievalItem

	if ce.pgOrMem() {
		rows, err := ce.pool.Query(ctx,
			`SELECT id, content, created_at, metadata
			 FROM platform_context_segments
			 WHERE session_id=$1 AND created_at >= $2
			 ORDER BY created_at DESC LIMIT 100`,
			req.SessionID, cutoff)
		if err != nil {
			return items
		}
		defer rows.Close()

		for rows.Next() {
			var id, content string
			var createdAt time.Time
			var metaBytes []byte
			if err := rows.Scan(&id, &content, &createdAt, &metaBytes); err != nil {
				continue
			}

			dw := 1.0
			var meta map[string]any
			if len(metaBytes) > 0 {
				_ = json.Unmarshal(metaBytes, &meta)
				if dwVal, ok := meta["decay_weight"].(float64); ok {
					dw = dwVal
				}
			}

			baseScore := keywordSimilarity(req.Query, content)
			score := baseScore * dw

			items = append(items, MemoryRetrievalItem{
				Type:      "episodic",
				Content:   content,
				Score:     score,
				Source:    "L1-episodic-PG",
				Timestamp: createdAt.Format(time.RFC3339),
			})
		}
		_ = qlower
	} else {
		ce.mu.RLock()
		defer ce.mu.RUnlock()

		for _, seg := range ce.segments {
			if req.SessionID != "" && seg.SessionID != req.SessionID {
				continue
			}
			createdAt, err := time.Parse(time.RFC3339, seg.CreatedAt)
			if err != nil || createdAt.Before(cutoff) {
				continue
			}
			dw := ce.getDecayWeight(seg)
			baseScore := keywordSimilarity(req.Query, seg.Content)
			score := baseScore * dw

			items = append(items, MemoryRetrievalItem{
				Type:      "episodic",
				Content:   seg.Content,
				Score:     score,
				Source:    "L1-episodic-mem",
				Timestamp: seg.CreatedAt,
			})
		}
	}

	sort.Slice(items, func(i, j int) bool {
		return items[i].Score > items[j].Score
	})

	return items
}

// retrieveProcedural performs 1-hop entity-relation graph traversal around mentioned entities.
func (ce *contextEngine) retrieveProcedural(query string) []MemoryRetrievalItem {
	var items []MemoryRetrievalItem
	seen := make(map[string]bool)

	// Find entities mentioned in the query
	var matchedEntities []Entity

	if ce.pgOrMem() {
		qlower := "%" + strings.ToLower(query) + "%"
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		rows, err := ce.pool.Query(ctx,
			`SELECT id, tenant_id, entity_type, name, description, properties, source, confidence,
			        last_seen_at, created_at, updated_at
			 FROM platform_entities
			 WHERE LOWER(name) LIKE $1 OR LOWER(description) LIKE $1
			 LIMIT 20`, qlower)
		if err != nil {
			return items
		}
		defer rows.Close()
		for rows.Next() {
			var ent Entity
			var propsBytes []byte
			var lastSeenAt, createdAt, updatedAt time.Time
			if err := rows.Scan(&ent.ID, &ent.TenantID, &ent.EntityType, &ent.Name, &ent.Description,
				&propsBytes, &ent.Source, &ent.Confidence, &lastSeenAt, &createdAt, &updatedAt); err != nil {
				continue
			}
			if len(propsBytes) > 0 {
				_ = json.Unmarshal(propsBytes, &ent.Properties)
			}
			if !lastSeenAt.IsZero() {
				ent.LastSeenAt = lastSeenAt.Format(time.RFC3339)
			}
			ent.CreatedAt = createdAt.Format(time.RFC3339)
			ent.UpdatedAt = updatedAt.Format(time.RFC3339)
			matchedEntities = append(matchedEntities, ent)
		}
	} else {
		ce.mu.RLock()
		qlower := strings.ToLower(query)
		for _, ent := range ce.entities {
			if strings.Contains(strings.ToLower(ent.Name), qlower) ||
				strings.Contains(strings.ToLower(ent.Description), qlower) {
				matchedEntities = append(matchedEntities, ent)
			}
		}
		ce.mu.RUnlock()
	}

	// For each matched entity, get 1-hop neighbors
	for _, ent := range matchedEntities {
		if !seen[ent.ID] {
			seen[ent.ID] = true
			content := fmt.Sprintf("[%s] %s: %s (confidence=%.2f)",
				ent.EntityType, ent.Name, ent.Description, ent.Confidence)
			items = append(items, MemoryRetrievalItem{
				Type:      "procedural",
				Content:   content,
				Score:     ent.Confidence,
				Source:    "L3-procedural",
				Timestamp: ent.UpdatedAt,
			})
		}

		// Get 1-hop relations
		var neighbors []Relation
		if ce.pgOrMem() {
			ctx2, cancel2 := context.WithTimeout(context.Background(), 2*time.Second)
			rRows, err := ce.pool.Query(ctx2,
				`SELECT id, subject_id, predicate, object_id, weight, evidence
				 FROM platform_relations
				 WHERE subject_id=$1 OR object_id=$1 LIMIT 50`, ent.ID)
			cancel2()
			if err == nil {
				for rRows.Next() {
					var rel Relation
					if err := rRows.Scan(&rel.ID, &rel.SubjectID, &rel.Predicate,
						&rel.ObjectID, &rel.Weight, &rel.Evidence); err != nil {
						continue
					}
					neighbors = append(neighbors, rel)
				}
				rRows.Close()
			}
		} else {
			ce.mu.RLock()
			for _, rel := range ce.relations {
				if rel.SubjectID == ent.ID || rel.ObjectID == ent.ID {
					neighbors = append(neighbors, rel)
				}
			}
			ce.mu.RUnlock()
		}

		for _, rel := range neighbors {
			relKey := rel.ID
			if !seen[relKey] {
				seen[relKey] = true
				// Resolve neighbor entity name
				neighborID := rel.ObjectID
				if rel.SubjectID == ent.ID {
					neighborID = rel.ObjectID
				} else {
					neighborID = rel.SubjectID
				}

				neighborName := neighborID
				if !ce.pgOrMem() {
					ce.mu.RLock()
					if ne, ok := ce.entities[neighborID]; ok {
						neighborName = ne.Name
					}
					ce.mu.RUnlock()
				}

				content := fmt.Sprintf("[relation] %s -[%s]-> %s (weight=%.2f): %s",
					ent.Name, rel.Predicate, neighborName, rel.Weight, rel.Evidence)
				items = append(items, MemoryRetrievalItem{
					Type:      "procedural",
					Content:   content,
					Score:     rel.Weight * ent.Confidence,
					Source:    "L3-procedural-graph",
					Timestamp: rel.CreatedAt,
				})
			}
		}
	}

	sort.Slice(items, func(i, j int) bool {
		return items[i].Score > items[j].Score
	})

	return items
}

// ── Conflict Resolution ───────────────────────────────────────────────

// handleDecisionsResolve triggers conflict resolution for a specific entity.
func (ce *contextEngine) handleDecisionsResolve(w http.ResponseWriter, r *http.Request) {
	var req struct {
		EntityID string `json:"entity_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if req.EntityID == "" {
		http.Error(w, `{"error":"entity_id is required"}`, http.StatusBadRequest)
		return
	}

	result := ce.resolveConflictsForEntity(r.Context(), req.EntityID)
	_ = json.NewEncoder(w).Encode(result)
}

// handleDecisionsConflicts lists all unresolved conflicts.
func (ce *contextEngine) handleDecisionsConflicts(w http.ResponseWriter, r *http.Request) {
	var conflicts []ConflictInfo

	if ce.pgOrMem() {
		ctx := r.Context()
		rows, err := ce.pool.Query(ctx,
			`SELECT id, entity_id, decision, similarity_score, reasoning, decided_at
			 FROM platform_memory_decisions
			 WHERE conflict_detected=true AND decision NOT IN ('NOOP')
			 ORDER BY decided_at DESC LIMIT 200`)
		if err != nil {
			_ = json.NewEncoder(w).Encode(map[string]interface{}{"conflicts": []ConflictInfo{}, "count": 0})
			return
		}
		defer rows.Close()

		for rows.Next() {
			var c ConflictInfo
			var entityIDPtr *string
			var simScore *float64
			var decidedAt time.Time
			if err := rows.Scan(&c.DecisionID, &entityIDPtr, &c.Decision, &simScore,
				&c.Reasoning, &decidedAt); err != nil {
				continue
			}
			if entityIDPtr != nil {
				c.EntityID = *entityIDPtr
			}
			if simScore != nil {
				c.SimilarityScore = *simScore
			}
			c.DecidedAt = decidedAt.Format(time.RFC3339)
			conflicts = append(conflicts, c)
		}
	} else {
		ce.mu.RLock()
		for _, dec := range ce.decisions {
			if dec.ConflictDetected && dec.Decision != "NOOP" {
				conflicts = append(conflicts, ConflictInfo{
					DecisionID:      dec.ID,
					EntityID:        dec.EntityID,
					Decision:        dec.Decision,
					SimilarityScore: dec.SimilarityScore,
					Reasoning:       dec.Reasoning,
					DecidedAt:       dec.DecidedAt,
				})
			}
		}
		ce.mu.RUnlock()
	}

	if conflicts == nil {
		conflicts = []ConflictInfo{}
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"conflicts": conflicts, "count": len(conflicts)})
}

// resolveConflictsForEntity resolves conflicts for a given entity by comparing
// recency, content length, and confidence.
func (ce *contextEngine) resolveConflictsForEntity(ctx context.Context, entityID string) ResolutionResult {
	var result ResolutionResult
	result.EntityID = entityID

	if ce.pgOrMem() {
		// Find conflicting decisions for this entity
		rows, err := ce.pool.Query(ctx,
			`SELECT id, decision, existing_memory, new_information, reasoning, similarity_score, decided_at
			 FROM platform_memory_decisions
			 WHERE entity_id=$1 AND conflict_detected=true AND decision NOT IN ('NOOP')
			 ORDER BY decided_at DESC LIMIT 10`, entityID)
		if err != nil {
			result.Resolution = "MANUAL"
			result.Reasoning = "Failed to query conflicts: " + err.Error()
			return result
		}
		defer rows.Close()

		var decs []decisionRef
		for rows.Next() {
			var d decisionRef
			var sim *float64
			var dt time.Time
			if err := rows.Scan(&d.id, &d.decision, &d.existingMemory, &d.newInformation,
				&d.reasoning, &sim, &dt); err != nil {
				continue
			}
			if sim != nil {
				d.similarity = *sim
			}
			d.decidedAt = dt
			decs = append(decs, d)
		}

		if len(decs) < 2 {
			result.Resolution = "NOT_ENOUGH_CONFLICTS"
			result.Reasoning = "Less than 2 conflicting decisions found; nothing to resolve"
			return result
		}

		// Auto-resolve by comparing recency, content length, confidence
		result = autoResolveConflicts(entityID, decs)

		// Record the resolution
		for _, d := range decs {
			_, _ = ce.pool.Exec(ctx,
				`UPDATE platform_memory_decisions SET decision='NOOP', reasoning=$1 WHERE id=$2`,
				"Auto-resolved by conflict resolver: "+result.Reasoning, d.id)
		}
	} else {
		ce.mu.Lock()
		var decs []decisionRef
		for _, dec := range ce.decisions {
			if dec.EntityID == entityID && dec.ConflictDetected && dec.Decision != "NOOP" {
				dt, _ := time.Parse(time.RFC3339, dec.DecidedAt)
				decs = append(decs, decisionRef{
					id:             dec.ID,
					decision:       dec.Decision,
					existingMemory: dec.ExistingMemory,
					newInformation: dec.NewInformation,
					reasoning:      dec.Reasoning,
					similarity:     dec.SimilarityScore,
					decidedAt:      dt,
				})
			}
		}
		ce.mu.Unlock()

		if len(decs) < 2 {
			result.Resolution = "NOT_ENOUGH_CONFLICTS"
			result.Reasoning = "Less than 2 conflicting decisions found"
			return result
		}

		result = autoResolveConflicts(entityID, decs)
	}

	log.Printf("context-engine: conflict resolved entity=%s resolution=%s reasoning=%s",
		entityID, result.Resolution, result.Reasoning)
	return result
}

// decisionRef holds a reference to a memory decision for conflict resolution.
type decisionRef struct {
	id             string
	decision       string
	existingMemory string
	newInformation string
	reasoning      string
	similarity     float64
	decidedAt      time.Time
}

// autoResolveConflicts compares conflicting entries to choose the best one.
func autoResolveConflicts(entityID string, decs []decisionRef) ResolutionResult {
	var bestIdx int
	bestScore := 0.0

	for i, d := range decs {
		// Score factors: recency (newer = better), content length (more info = better), similarity confidence
		ageScore := 1.0 / (1.0 + time.Since(d.decidedAt).Hours()/24.0) // decays with age
		lenScore := math.Log1p(float64(len(d.newInformation)+len(d.existingMemory))) / math.Log1p(1000)
		simScore := d.similarity

		totalScore := ageScore*0.4 + lenScore*0.3 + simScore*0.3
		if totalScore > bestScore {
			bestScore = totalScore
			bestIdx = i
		}
	}

	chosen := decs[bestIdx]
	var rejectedDesc string
	if bestIdx > 0 {
		rejectedDesc = decs[0].newInformation
	} else if len(decs) > 1 {
		rejectedDesc = decs[1].newInformation
	}

	return ResolutionResult{
		EntityID:   entityID,
		Resolution: "PREFER_NEWER",
		Chosen:     fmt.Sprintf("decision_id=%s info=%s decided=%s", chosen.id, truncateForLLM(chosen.newInformation, 100), chosen.decidedAt.Format(time.RFC3339)),
		Rejected:   truncateForLLM(rejectedDesc, 100),
		Reasoning:  fmt.Sprintf("Auto-resolved by comparing recency (weight=0.4), content length (weight=0.3), and confidence (weight=0.3). Best score=%.3f for decision %s", bestScore, chosen.id),
	}
}

// ── Math / Similarity Helpers ─────────────────────────────────────────

// cosineSimilarity computes the cosine similarity between two float64 slices.
func cosineSimilarity(a, b []float64) float64 {
	if len(a) == 0 || len(b) == 0 || len(a) != len(b) {
		return 0
	}
	var dot, normA, normB float64
	for i := range a {
		dot += a[i] * b[i]
		normA += a[i] * a[i]
		normB += b[i] * b[i]
	}
	if normA == 0 || normB == 0 {
		return 0
	}
	return dot / (math.Sqrt(normA) * math.Sqrt(normB))
}

// keywordSimilarity is a simple TF-based similarity score between a query and content.
func keywordSimilarity(query, content string) float64 {
	queryLower := strings.ToLower(query)
	contentLower := strings.ToLower(content)

	queryWords := strings.Fields(queryLower)
	if len(queryWords) == 0 {
		return 0
	}

	hits := 0
	for _, w := range queryWords {
		if len(w) < 2 {
			continue
		}
		if strings.Contains(contentLower, w) {
			hits++
		}
	}

	score := float64(hits) / float64(len(queryWords))
	return score
}

// toFloat64Slice converts an interface{} representing a slice of numbers to []float64.
func toFloat64Slice(v interface{}) []float64 {
	switch arr := v.(type) {
	case []float64:
		return arr
	case []interface{}:
		result := make([]float64, 0, len(arr))
		for _, item := range arr {
			if f, ok := item.(float64); ok {
				result = append(result, f)
			}
		}
		return result
	case []float32:
		result := make([]float64, len(arr))
		for i, f := range arr {
			result[i] = float64(f)
		}
		return result
	}
	return nil
}

// contentHash returns a hex SHA-256 hash of the content for deduplication.
func contentHash(content string) string {
	h := sha256.Sum256([]byte(content))
	return hex.EncodeToString(h[:16]) // first 16 bytes is sufficient for dedup
}
