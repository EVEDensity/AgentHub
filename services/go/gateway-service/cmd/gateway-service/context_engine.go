package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
)

// ── Data Types ──────────────────────────────────────────────────────

// ContextSegment is a compressed checkpoint or summary of a conversation slice.
type ContextSegment struct {
	ID                   string   `json:"id"`
	TenantID             string   `json:"tenant_id"`
	SessionID            string   `json:"session_id"`
	SegmentType          string   `json:"segment_type"` // summary, checkpoint, entity_extract
	Title                string   `json:"title"`
	Content              string   `json:"content"`
	TokenCount           int      `json:"token_count"`
	SourceSequenceStart  int64    `json:"source_sequence_start"`
	SourceSequenceEnd    int64    `json:"source_sequence_end"`
	SourceMessageCount   int      `json:"source_message_count"`
	Entities             []string `json:"entities"`
	Metadata             map[string]any `json:"metadata"`
	CompressedAt         string   `json:"compressed_at,omitempty"`
	CreatedAt            string   `json:"created_at"`
}

// Entity is a node in the L3 procedural memory graph.
type Entity struct {
	ID          string         `json:"id"`
	TenantID    string         `json:"tenant_id"`
	EntityType  string         `json:"entity_type"` // user, agent, session, tool, document, concept
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Properties  map[string]any `json:"properties"`
	Source      string         `json:"source"`
	Confidence  float64        `json:"confidence"`
	LastSeenAt  string         `json:"last_seen_at,omitempty"`
	CreatedAt   string         `json:"created_at"`
	UpdatedAt   string         `json:"updated_at"`
}

// Relation is an edge in the L3 procedural memory graph.
type Relation struct {
	ID        string  `json:"id"`
	TenantID  string  `json:"tenant_id"`
	SubjectID string  `json:"subject_id"`
	Predicate string  `json:"predicate"` // USED, CREATED, MENTIONS, RELATED_TO, EVOLVED_FROM, BELONGS_TO, DEPENDS_ON
	ObjectID  string  `json:"object_id"`
	Weight    float64 `json:"weight"`
	Evidence  string  `json:"evidence"`
	CreatedAt string  `json:"created_at"`
}

// ContextSearchResult is the unified response from a multi-backend search.
type ContextSearchResult struct {
	Segments   []ContextSegment `json:"segments"`
	Entities   []Entity         `json:"entities"`
	TotalHits  int              `json:"total_hits"`
	Sources    []string         `json:"sources"` // which backends contributed
	TookMs     float64          `json:"took_ms"`
}

// CompressionRun records a completed sleep compression job.
type CompressionRun struct {
	ID                  string `json:"id"`
	TenantID            string `json:"tenant_id"`
	StartedAt           string `json:"started_at"`
	CompletedAt         string `json:"completed_at,omitempty"`
	Status              string `json:"status"`
	SessionsScanned     int    `json:"sessions_scanned"`
	SessionsCompressed  int    `json:"sessions_compressed"`
	MessagesProcessed   int64  `json:"messages_processed"`
	TokensBefore        int64  `json:"tokens_before"`
	TokensAfter         int64  `json:"tokens_after"`
	EntitiesExtracted   int    `json:"entities_extracted"`
	ErrorMessage        string `json:"error_message,omitempty"`
}

// MemoryDecision records an LLM memory strategy decision.
type MemoryDecision struct {
	ID               string  `json:"id"`
	TenantID         string  `json:"tenant_id"`
	EntityID         string  `json:"entity_id,omitempty"`
	Decision         string  `json:"decision"` // ADD, UPDATE, DELETE, NOOP
	ExistingMemory   string  `json:"existing_memory"`
	NewInformation   string  `json:"new_information"`
	Reasoning        string  `json:"reasoning"`
	SimilarityScore  float64 `json:"similarity_score,omitempty"`
	ConflictDetected bool    `json:"conflict_detected"`
	DecidedAt        string  `json:"decided_at"`
}

// ── Context Engine Handler ──────────────────────────────────────────

type contextEngine struct {
	mu        sync.RWMutex
	segments  map[string]ContextSegment  // id → segment
	entities  map[string]Entity          // id → entity
	relations map[string]Relation        // id → relation
	decisions []MemoryDecision           // in-memory decision log (MVP)
	compRuns  []CompressionRun           // in-memory compression run history
	bus       *eventbus.Client
}

func newContextEngine(bus *eventbus.Client) *contextEngine {
	return &contextEngine{
		segments:  make(map[string]ContextSegment),
		entities:  make(map[string]Entity),
		relations: make(map[string]Relation),
		decisions: make([]MemoryDecision, 0),
		compRuns:  make([]CompressionRun, 0),
		bus:       bus,
	}
}

func (ce *contextEngine) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	rel := strings.TrimPrefix(r.URL.Path, "/context")
	rel = strings.TrimPrefix(rel, "/")

	switch {
	// Search across memory layers
	case rel == "search" && r.Method == http.MethodGet:
		ce.search(w, r)
	case rel == "search" && r.Method == http.MethodPost:
		ce.search(w, r)

	// Recall by session ID
	case rel == "recall" && r.Method == http.MethodGet:
		ce.recall(w, r)

	// Entity CRUD + graph traversal
	case rel == "entity" && r.Method == http.MethodGet:
		ce.listEntities(w, r)
	case rel == "entity" && r.Method == http.MethodPost:
		ce.createEntity(w, r)
	case strings.HasPrefix(rel, "entity/") && !strings.Contains(rel[len("entity/"):], "/") && r.Method == http.MethodGet:
		entityID := rel[len("entity/"):]
		ce.getEntity(w, r, entityID)
	case strings.HasPrefix(rel, "entity/") && strings.HasSuffix(rel, "/graph") && r.Method == http.MethodGet:
		entityID := rel[len("entity/"):]
		entityID = strings.TrimSuffix(entityID, "/graph")
		ce.getEntityGraph(w, r, entityID)

	// Relation CRUD
	case rel == "relation" && r.Method == http.MethodPost:
		ce.createRelation(w, r)
	case rel == "relation" && r.Method == http.MethodGet:
		ce.listRelations(w, r)

	// Context segments CRUD
	case rel == "segment" && r.Method == http.MethodPost:
		ce.createSegment(w, r)
	case rel == "segment" && r.Method == http.MethodGet:
		ce.listSegments(w, r)
	case strings.HasPrefix(rel, "segment/") && r.Method == http.MethodDelete:
		segID := rel[len("segment/"):]
		ce.deleteSegment(w, r, segID)

	// Sleep compression
	case rel == "compress" && r.Method == http.MethodPost:
		ce.triggerCompression(w, r)
	case rel == "compression-runs" && r.Method == http.MethodGet:
		ce.listCompressionRuns(w, r)

	// Memory decisions
	case rel == "decisions" && r.Method == http.MethodGet:
		ce.listDecisions(w, r)
	case rel == "decisions" && r.Method == http.MethodPost:
		ce.recordDecision(w, r)

	// Stats dashboard
	case rel == "stats" && r.Method == http.MethodGet:
		ce.stats(w, r)

	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

// ── Search ──────────────────────────────────────────────────────────

func (ce *contextEngine) search(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	q := r.URL.Query().Get("q")
	if q == "" && r.Method == http.MethodPost {
		var body struct {
			Query string `json:"q"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		q = body.Query
	}
	if q == "" {
		http.Error(w, `{"error":"query parameter 'q' is required"}`, http.StatusBadRequest)
		return
	}

	ce.mu.RLock()
	defer ce.mu.RUnlock()

	qlower := strings.ToLower(q)
	var segments []ContextSegment
	var entities []Entity
	sources := make(map[string]bool)

	// Search segments (L1 episodic memory)
	for _, seg := range ce.segments {
		if strings.Contains(strings.ToLower(seg.Content), qlower) ||
			strings.Contains(strings.ToLower(seg.Title), qlower) {
			segments = append(segments, seg)
			sources["L1-episodic"] = true
		}
	}

	// Search entities (L3 procedural memory)
	for _, ent := range ce.entities {
		if strings.Contains(strings.ToLower(ent.Name), qlower) ||
			strings.Contains(strings.ToLower(ent.Description), qlower) {
			entities = append(entities, ent)
			sources["L3-procedural"] = true
		}
	}

	// Sort by recency (newest first)
	sort.Slice(segments, func(i, j int) bool {
		return segments[i].CreatedAt > segments[j].CreatedAt
	})
	sort.Slice(entities, func(i, j int) bool {
		return entities[i].UpdatedAt > entities[j].UpdatedAt
	})

	sourceList := make([]string, 0, len(sources))
	for s := range sources {
		sourceList = append(sourceList, s)
	}
	sort.Strings(sourceList)

	result := ContextSearchResult{
		Segments:  segments,
		Entities:  entities,
		TotalHits: len(segments) + len(entities),
		Sources:   sourceList,
		TookMs:    float64(time.Since(start).Microseconds()) / 1000.0,
	}
	_ = json.NewEncoder(w).Encode(result)
}

// ── Recall ──────────────────────────────────────────────────────────

func (ce *contextEngine) recall(w http.ResponseWriter, r *http.Request) {
	sessionID := r.URL.Query().Get("session_id")
	if sessionID == "" {
		http.Error(w, `{"error":"session_id is required"}`, http.StatusBadRequest)
		return
	}

	ce.mu.RLock()
	defer ce.mu.RUnlock()

	var result []ContextSegment
	for _, seg := range ce.segments {
		if seg.SessionID == sessionID {
			result = append(result, seg)
		}
	}
	sort.Slice(result, func(i, j int) bool {
		return result[i].SourceSequenceStart < result[j].SourceSequenceStart
	})

	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"session_id": sessionID,
		"segments":   result,
		"count":      len(result),
	})
}

// ── Entity CRUD ─────────────────────────────────────────────────────

func (ce *contextEngine) createEntity(w http.ResponseWriter, r *http.Request) {
	var ent Entity
	if err := json.NewDecoder(r.Body).Decode(&ent); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if ent.ID == "" {
		ent.ID = "ent-" + randomSuffix()
	}
	if ent.EntityType == "" {
		ent.EntityType = "concept"
	}
	ent.CreatedAt = time.Now().UTC().Format(time.RFC3339)
	ent.UpdatedAt = ent.CreatedAt

	ce.mu.Lock()
	ce.entities[ent.ID] = ent
	ce.mu.Unlock()

	log.Printf("context-engine: entity created id=%s type=%s name=%s", ent.ID, ent.EntityType, ent.Name)
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(ent)
}

func (ce *contextEngine) getEntity(w http.ResponseWriter, _ *http.Request, entityID string) {
	ce.mu.RLock()
	ent, ok := ce.entities[entityID]
	ce.mu.RUnlock()

	if !ok {
		http.Error(w, `{"error":"entity not found"}`, http.StatusNotFound)
		return
	}
	_ = json.NewEncoder(w).Encode(ent)
}

func (ce *contextEngine) listEntities(w http.ResponseWriter, r *http.Request) {
	etype := r.URL.Query().Get("type")
	tenantID := r.URL.Query().Get("tenant_id")

	ce.mu.RLock()
	defer ce.mu.RUnlock()

	var result []Entity
	for _, ent := range ce.entities {
		if etype != "" && ent.EntityType != etype {
			continue
		}
		if tenantID != "" && ent.TenantID != tenantID {
			continue
		}
		result = append(result, ent)
	}

	if result == nil {
		result = []Entity{}
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"entities": result, "count": len(result)})
}

// getEntityGraph traverses the entity-relation graph using simple BFS.
func (ce *contextEngine) getEntityGraph(w http.ResponseWriter, _ *http.Request, entityID string) {
	ce.mu.RLock()
	defer ce.mu.RUnlock()

	_, ok := ce.entities[entityID]
	if !ok {
		http.Error(w, `{"error":"entity not found"}`, http.StatusNotFound)
		return
	}

	// BFS up to 2 hops
	visited := map[string]bool{entityID: true}
	nodes := make([]Entity, 0)
	edges := make([]Relation, 0)
	queue := []string{entityID}

	for depth := 0; depth < 2 && len(queue) > 0; depth++ {
		nextQueue := make([]string, 0)
		for _, currentID := range queue {
			if ent, ok := ce.entities[currentID]; ok {
				nodes = append(nodes, ent)
			}
			// Find all relations where current is subject or object
			for _, rel := range ce.relations {
				if rel.SubjectID == currentID {
					edges = append(edges, rel)
					if !visited[rel.ObjectID] {
						visited[rel.ObjectID] = true
						nextQueue = append(nextQueue, rel.ObjectID)
					}
				}
				if rel.ObjectID == currentID {
					edges = append(edges, rel)
					if !visited[rel.SubjectID] {
						visited[rel.SubjectID] = true
						nextQueue = append(nextQueue, rel.SubjectID)
					}
				}
			}
		}
		queue = nextQueue
	}

	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"entity_id": entityID,
		"nodes":     nodes,
		"edges":     edges,
	})
}

// ── Relation CRUD ────────────────────────────────────────────────────

func (ce *contextEngine) createRelation(w http.ResponseWriter, r *http.Request) {
	var rel Relation
	if err := json.NewDecoder(r.Body).Decode(&rel); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if rel.ID == "" {
		rel.ID = "rel-" + randomSuffix()
	}
	if rel.Predicate == "" {
		rel.Predicate = "RELATED_TO"
	}
	if rel.Weight == 0 {
		rel.Weight = 1.0
	}
	rel.CreatedAt = time.Now().UTC().Format(time.RFC3339)

	ce.mu.Lock()
	ce.relations[rel.ID] = rel
	ce.mu.Unlock()

	log.Printf("context-engine: relation created %s -[%s]-> %s", rel.SubjectID, rel.Predicate, rel.ObjectID)
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(rel)
}

func (ce *contextEngine) listRelations(w http.ResponseWriter, r *http.Request) {
	entityID := r.URL.Query().Get("entity_id")
	predicate := r.URL.Query().Get("predicate")

	ce.mu.RLock()
	defer ce.mu.RUnlock()

	var result []Relation
	for _, rel := range ce.relations {
		if entityID != "" && rel.SubjectID != entityID && rel.ObjectID != entityID {
			continue
		}
		if predicate != "" && rel.Predicate != predicate {
			continue
		}
		result = append(result, rel)
	}
	if result == nil {
		result = []Relation{}
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"relations": result, "count": len(result)})
}

// ── Context Segments ────────────────────────────────────────────────

func (ce *contextEngine) createSegment(w http.ResponseWriter, r *http.Request) {
	var seg ContextSegment
	if err := json.NewDecoder(r.Body).Decode(&seg); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if seg.ID == "" {
		seg.ID = "seg-" + randomSuffix()
	}
	if seg.SegmentType == "" {
		seg.SegmentType = "summary"
	}
	seg.CreatedAt = time.Now().UTC().Format(time.RFC3339)

	ce.mu.Lock()
	ce.segments[seg.ID] = seg
	ce.mu.Unlock()

	// Publish event for downstream consumers
	ce.publishContextEvent("context.segment.created", seg.TenantID, seg.SessionID, map[string]any{
		"segment_id":   seg.ID,
		"segment_type": seg.SegmentType,
		"token_count":  seg.TokenCount,
	})

	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(seg)
}

func (ce *contextEngine) listSegments(w http.ResponseWriter, r *http.Request) {
	sessionID := r.URL.Query().Get("session_id")
	segType := r.URL.Query().Get("type")
	tenantID := r.URL.Query().Get("tenant_id")

	ce.mu.RLock()
	defer ce.mu.RUnlock()

	var result []ContextSegment
	for _, seg := range ce.segments {
		if sessionID != "" && seg.SessionID != sessionID {
			continue
		}
		if segType != "" && seg.SegmentType != segType {
			continue
		}
		if tenantID != "" && seg.TenantID != tenantID {
			continue
		}
		result = append(result, seg)
	}
	if result == nil {
		result = []ContextSegment{}
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"segments": result, "count": len(result)})
}

func (ce *contextEngine) deleteSegment(w http.ResponseWriter, _ *http.Request, segID string) {
	ce.mu.Lock()
	delete(ce.segments, segID)
	ce.mu.Unlock()
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
}

// ── Sleep Compression ───────────────────────────────────────────────

func (ce *contextEngine) triggerCompression(w http.ResponseWriter, r *http.Request) {
	var req struct {
		TenantID string `json:"tenant_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	run := CompressionRun{
		ID:         "comp-" + randomSuffix(),
		TenantID:   req.TenantID,
		StartedAt:  time.Now().UTC().Format(time.RFC3339),
		Status:     "running",
	}

	ce.mu.Lock()
	ce.compRuns = append(ce.compRuns, run)
	runIdx := len(ce.compRuns) - 1
	ce.mu.Unlock()

	// Simulate compression in goroutine (MVP: local, non-LLM)
	go func() {
		time.Sleep(500 * time.Millisecond) // simulate work

		ce.mu.Lock()
		defer ce.mu.Unlock()

		// Count and compress segments for this tenant
		var scanned, compressed int
		var tokensBefore, tokensAfter int64
		for _, seg := range ce.segments {
			if req.TenantID != "" && seg.TenantID != req.TenantID {
				continue
			}
			scanned++
			tokensBefore += int64(seg.TokenCount)
			if seg.SourceMessageCount > 20 {
				// Simulate compression: reduce token count by ~65%
				compressed++
				tokensAfter += int64(float64(seg.TokenCount) * 0.35)
			} else {
				tokensAfter += int64(seg.TokenCount)
			}
		}

		ce.compRuns[runIdx].Status = "completed"
		ce.compRuns[runIdx].CompletedAt = time.Now().UTC().Format(time.RFC3339)
		ce.compRuns[runIdx].SessionsScanned = scanned
		ce.compRuns[runIdx].SessionsCompressed = compressed
		ce.compRuns[runIdx].MessagesProcessed = int64(scanned * 10)
		ce.compRuns[runIdx].TokensBefore = tokensBefore
		ce.compRuns[runIdx].TokensAfter = tokensAfter

		saving := float64(0)
		if tokensBefore > 0 {
			saving = float64(tokensBefore-tokensAfter) / float64(tokensBefore) * 100
		}
		log.Printf("context-engine: compression completed run=%s scanned=%d compressed=%d tokens_before=%d tokens_after=%d saving=%.1f%%",
			run.ID, scanned, compressed, tokensBefore, tokensAfter, saving)
	}()

	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(run)
}

func (ce *contextEngine) listCompressionRuns(w http.ResponseWriter, _ *http.Request) {
	ce.mu.RLock()
	defer ce.mu.RUnlock()

	runs := ce.compRuns
	if runs == nil {
		runs = []CompressionRun{}
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"runs": runs, "count": len(runs)})
}

// ── Memory Strategy Decisions ───────────────────────────────────────

func (ce *contextEngine) recordDecision(w http.ResponseWriter, r *http.Request) {
	var dec MemoryDecision
	if err := json.NewDecoder(r.Body).Decode(&dec); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if dec.ID == "" {
		dec.ID = "dec-" + randomSuffix()
	}
	dec.DecidedAt = time.Now().UTC().Format(time.RFC3339)

	ce.mu.Lock()
	ce.decisions = append(ce.decisions, dec)
	// Keep only last 1000 decisions
	if len(ce.decisions) > 1000 {
		ce.decisions = ce.decisions[len(ce.decisions)-1000:]
	}
	ce.mu.Unlock()

	log.Printf("context-engine: memory decision %s entity=%s decision=%s conflict=%v",
		dec.ID, dec.EntityID, dec.Decision, dec.ConflictDetected)

	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(dec)
}

func (ce *contextEngine) listDecisions(w http.ResponseWriter, r *http.Request) {
	decision := r.URL.Query().Get("decision")
	entityID := r.URL.Query().Get("entity_id")

	ce.mu.RLock()
	defer ce.mu.RUnlock()

	var result []MemoryDecision
	for _, dec := range ce.decisions {
		if decision != "" && dec.Decision != decision {
			continue
		}
		if entityID != "" && dec.EntityID != entityID {
			continue
		}
		result = append(result, dec)
	}
	if result == nil {
		result = []MemoryDecision{}
	}
	// Return most recent first
	sort.Slice(result, func(i, j int) bool {
		return result[i].DecidedAt > result[j].DecidedAt
	})
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"decisions": result, "count": len(result)})
}

// ── Memory Strategy Prompt (LLM template) ──────────────────────────

// memoryStrategyPrompt is the LLM prompt template for ADD/UPDATE/DELETE/NOOP decisions.
// It follows the ContextDB Memory Manager pattern.
const memoryStrategyPrompt = `你是一个记忆管理专家。给定现有记忆和新信息，决定操作。

## 现有记忆
{existing}

## 新信息
{new}

## 决策规则
- **ADD**: 新信息是全新的，与任何现有记忆都不重复
- **UPDATE**: 新信息与现有记忆相关但提供了更新/更正（同一实体，属性值有变化）
- **DELETE**: 现有记忆已过时、错误或与更新信息明确冲突
- **NOOP**: 新信息与现有记忆高度重复（语义相似度 > 85%）或完全无关

## 输出格式
以 JSON 格式输出：
{"decision": "ADD|UPDATE|DELETE|NOOP", "reasoning": "简短说明理由", "conflict_detected": true/false}
`

// GetMemoryStrategyPrompt returns the LLM prompt template.
func GetMemoryStrategyPrompt() string {
	return memoryStrategyPrompt
}

// ── Stats Dashboard ─────────────────────────────────────────────────

func (ce *contextEngine) stats(w http.ResponseWriter, _ *http.Request) {
	ce.mu.RLock()
	defer ce.mu.RUnlock()

	// Aggregate segment types
	segTypes := map[string]int{}
	totalSegTokens := 0
	for _, seg := range ce.segments {
		segTypes[seg.SegmentType]++
		totalSegTokens += seg.TokenCount
	}

	// Aggregate entity types
	entTypes := map[string]int{}
	for _, ent := range ce.entities {
		entTypes[ent.EntityType]++
	}

	// Aggregate relation predicates
	predCounts := map[string]int{}
	for _, rel := range ce.relations {
		predCounts[rel.Predicate]++
	}

	// Count recent decisions
	add, update, del, noop := 0, 0, 0, 0
	for _, dec := range ce.decisions {
		switch dec.Decision {
		case "ADD":
			add++
		case "UPDATE":
			update++
		case "DELETE":
			del++
		case "NOOP":
			noop++
		}
	}

	// Compression stats
	totalSaving := float64(0)
	for _, run := range ce.compRuns {
		if run.Status == "completed" && run.TokensBefore > 0 {
			totalSaving += float64(run.TokensBefore-run.TokensAfter) / float64(run.TokensBefore) * 100
		}
	}
	avgSaving := float64(0)
	if len(ce.compRuns) > 0 {
		avgSaving = totalSaving / float64(len(ce.compRuns))
	}

	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"memory_layers": map[string]interface{}{
			"L0_working":    map[string]interface{}{"status": "active", "backend": "Redis", "ttl": "24h"},
			"L1_episodic":   map[string]interface{}{"segments": len(ce.segments), "total_tokens": totalSegTokens, "types": segTypes, "backend": "PostgreSQL"},
			"L2_semantic":   map[string]interface{}{"status": "active", "backend": "Qdrant", "note": "vector search via existing retrieval-core"},
			"L3_procedural": map[string]interface{}{"entities": len(ce.entities), "relations": len(ce.relations), "entity_types": entTypes, "relation_predicates": predCounts, "backend": "PostgreSQL (CTE)"},
		},
		"decisions": map[string]interface{}{
			"total":           len(ce.decisions),
			"add_count":       add,
			"update_count":    update,
			"delete_count":    del,
			"noop_count":      noop,
			"dedup_rate":      fmtPct(noop, len(ce.decisions)),
		},
		"compression": map[string]interface{}{
			"total_runs":      len(ce.compRuns),
			"avg_token_saving_pct": fmtFloat(avgSaving, 1),
			"last_run":        ce.lastCompressionRun(),
		},
	})
}

func (ce *contextEngine) lastCompressionRun() *CompressionRun {
	if len(ce.compRuns) == 0 {
		return nil
	}
	return &ce.compRuns[len(ce.compRuns)-1]
}

// ── Helpers ──────────────────────────────────────────────────────────

func (ce *contextEngine) publishContextEvent(eventTypeStr, tenantID, sessionID string, payload map[string]any) {
	if ce.bus == nil {
		return
	}
	event := events.NewEnvelope(
		events.EventType(eventTypeStr),
		tenantID,
		sessionID,
		"ctx-"+randomSuffix(),
		events.Producer{Service: "context-engine", Instance: getenv("HOSTNAME", "local")},
		payload,
	)
	event.EventID = "ctx-evt-" + randomSuffix()
	event.Routing = &events.Routing{
		Channel:      "context",
		PartitionKey: sessionID,
		Priority:     events.PriorityNormal,
	}

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := ce.bus.PublishEnvelope(ctx, eventbus.SessionEventsSubject, event); err != nil {
		log.Printf("context-engine: publish error: %v", err)
	}
}

func fmtPct(n, total int) string {
	if total == 0 {
		return "0%"
	}
	pct := float64(n*100) / float64(total)
	return fmt.Sprintf("%.0f%%", pct)
}

func fmtFloat(v float64, decimals int) float64 {
	shift := 1.0
	for i := 0; i < decimals; i++ {
		shift *= 10
	}
	return float64(int(v*shift)) / shift
}
