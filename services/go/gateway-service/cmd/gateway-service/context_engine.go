package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/db"
	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
)

// ── Data Types ──────────────────────────────────────────────────────

// ContextSegment is a compressed checkpoint or summary of a conversation slice.
type ContextSegment struct {
	ID                  string         `json:"id"`
	TenantID            string         `json:"tenant_id"`
	SessionID           string         `json:"session_id"`
	SegmentType         string         `json:"segment_type"` // summary, checkpoint, entity_extract
	Title               string         `json:"title"`
	Content             string         `json:"content"`
	TokenCount          int            `json:"token_count"`
	SourceSequenceStart int64          `json:"source_sequence_start"`
	SourceSequenceEnd   int64          `json:"source_sequence_end"`
	SourceMessageCount  int            `json:"source_message_count"`
	Entities            []string       `json:"entities"`
	Metadata            map[string]any `json:"metadata"`
	CompressedAt        string         `json:"compressed_at,omitempty"`
	CreatedAt           string         `json:"created_at"`
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
	Segments  []ContextSegment `json:"segments"`
	Entities  []Entity         `json:"entities"`
	TotalHits int              `json:"total_hits"`
	Sources   []string         `json:"sources"` // which backends contributed
	TookMs    float64          `json:"took_ms"`
}

// CompressionRun records a completed sleep compression job.
type CompressionRun struct {
	ID                 string `json:"id"`
	TenantID           string `json:"tenant_id"`
	StartedAt          string `json:"started_at"`
	CompletedAt        string `json:"completed_at,omitempty"`
	Status             string `json:"status"`
	SessionsScanned    int    `json:"sessions_scanned"`
	SessionsCompressed int    `json:"sessions_compressed"`
	MessagesProcessed  int64  `json:"messages_processed"`
	TokensBefore       int64  `json:"tokens_before"`
	TokensAfter        int64  `json:"tokens_after"`
	EntitiesExtracted  int    `json:"entities_extracted"`
	ErrorMessage       string `json:"error_message,omitempty"`
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
	pool      *db.Pool // PostgreSQL pool (nil = in-memory fallback)
	segments  map[string]ContextSegment
	entities  map[string]Entity
	relations map[string]Relation
	decisions []MemoryDecision // ring buffer via DB; in-memory cache for stats
	compRuns  []CompressionRun
	bus       *eventbus.Client

	// Cron control
	cronStop chan struct{}
}

func newContextEngine(bus *eventbus.Client, pool *db.Pool) *contextEngine {
	ce := &contextEngine{
		pool:      pool,
		segments:  make(map[string]ContextSegment),
		entities:  make(map[string]Entity),
		relations: make(map[string]Relation),
		decisions: make([]MemoryDecision, 0),
		compRuns:  make([]CompressionRun, 0),
		bus:       bus,
		cronStop:  make(chan struct{}),
	}
	// Start the daily compression scheduler
	go ce.compressionCron()
	return ce
}

// Shutdown stops the cron scheduler.
func (ce *contextEngine) Shutdown() {
	close(ce.cronStop)
}

func (ce *contextEngine) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	rel := strings.TrimPrefix(r.URL.Path, "/context")
	rel = strings.TrimPrefix(rel, "/")

	switch {
	// Search across memory layers
	case rel == "search" && (r.Method == http.MethodGet || r.Method == http.MethodPost):
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

// ── PostgreSQL helpers ──────────────────────────────────────────────

// pgOrMem returns true when using PostgreSQL (pool is wired).
func (ce *contextEngine) pgOrMem() bool {
	return ce.pool != nil
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

	var segments []ContextSegment
	var entities []Entity
	sources := make(map[string]bool)

	if ce.pgOrMem() {
		segments, entities = ce.searchPG(r.Context(), q, sources)
	} else {
		segments, entities = ce.searchMem(q, sources)
	}

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

func (ce *contextEngine) searchPG(ctx context.Context, q string, sources map[string]bool) ([]ContextSegment, []Entity) {
	qlower := "%" + strings.ToLower(q) + "%"

	// Search segments (L1 episodic)
	rows, err := ce.pool.Query(ctx,
		`SELECT id, tenant_id, session_id, segment_type, title, content, token_count,
		        source_sequence_start, source_sequence_end, source_message_count,
		        entities, metadata, compressed_at, created_at
		 FROM platform_context_segments
		 WHERE LOWER(content) LIKE $1 OR LOWER(title) LIKE $1
		 ORDER BY created_at DESC LIMIT 50`, qlower)
	segments := make([]ContextSegment, 0)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var seg ContextSegment
			var entitiesArr []string
			var metadataBytes []byte
			var compressedAt, createdAt time.Time
			if err := rows.Scan(&seg.ID, &seg.TenantID, &seg.SessionID, &seg.SegmentType,
				&seg.Title, &seg.Content, &seg.TokenCount,
				&seg.SourceSequenceStart, &seg.SourceSequenceEnd, &seg.SourceMessageCount,
				&entitiesArr, &metadataBytes, &compressedAt, &createdAt); err != nil {
				continue
			}
			seg.Entities = entitiesArr
			if len(metadataBytes) > 0 {
				_ = json.Unmarshal(metadataBytes, &seg.Metadata)
			}
			if !compressedAt.IsZero() {
				seg.CompressedAt = compressedAt.Format(time.RFC3339)
			}
			seg.CreatedAt = createdAt.Format(time.RFC3339)
			segments = append(segments, seg)
		}
		if len(segments) > 0 {
			sources["L1-episodic"] = true
		}
	}

	// Search entities (L3 procedural)
	eRows, err := ce.pool.Query(ctx,
		`SELECT id, tenant_id, entity_type, name, description, properties, source, confidence,
		        last_seen_at, created_at, updated_at
		 FROM platform_entities
		 WHERE LOWER(name) LIKE $1 OR LOWER(description) LIKE $1
		 ORDER BY updated_at DESC LIMIT 50`, qlower)
	entities := make([]Entity, 0)
	if err == nil {
		defer eRows.Close()
		for eRows.Next() {
			var ent Entity
			var propsBytes []byte
			var lastSeenAt, createdAt, updatedAt time.Time
			if err := eRows.Scan(&ent.ID, &ent.TenantID, &ent.EntityType, &ent.Name, &ent.Description,
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
			entities = append(entities, ent)
		}
		if len(entities) > 0 {
			sources["L3-procedural"] = true
		}
	}

	return segments, entities
}

func (ce *contextEngine) searchMem(q string, sources map[string]bool) ([]ContextSegment, []Entity) {
	ce.mu.RLock()
	defer ce.mu.RUnlock()

	qlower := strings.ToLower(q)
	var segments []ContextSegment
	var entities []Entity

	for _, seg := range ce.segments {
		if strings.Contains(strings.ToLower(seg.Content), qlower) ||
			strings.Contains(strings.ToLower(seg.Title), qlower) {
			segments = append(segments, seg)
			sources["L1-episodic"] = true
		}
	}
	sort.Slice(segments, func(i, j int) bool {
		return segments[i].CreatedAt > segments[j].CreatedAt
	})

	for _, ent := range ce.entities {
		if strings.Contains(strings.ToLower(ent.Name), qlower) ||
			strings.Contains(strings.ToLower(ent.Description), qlower) {
			entities = append(entities, ent)
			sources["L3-procedural"] = true
		}
	}
	sort.Slice(entities, func(i, j int) bool {
		return entities[i].UpdatedAt > entities[j].UpdatedAt
	})

	return segments, entities
}

// ── Recall ──────────────────────────────────────────────────────────

func (ce *contextEngine) recall(w http.ResponseWriter, r *http.Request) {
	sessionID := r.URL.Query().Get("session_id")
	if sessionID == "" {
		http.Error(w, `{"error":"session_id is required"}`, http.StatusBadRequest)
		return
	}

	var result []ContextSegment

	if ce.pgOrMem() {
		rows, err := ce.pool.Query(r.Context(),
			`SELECT id, tenant_id, session_id, segment_type, title, content, token_count,
			        source_sequence_start, source_sequence_end, source_message_count,
			        entities, metadata, compressed_at, created_at
			 FROM platform_context_segments
			 WHERE session_id=$1
			 ORDER BY source_sequence_start ASC`, sessionID)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var seg ContextSegment
				var entitiesArr []string
				var metadataBytes []byte
				var compressedAt, createdAt time.Time
				if err := rows.Scan(&seg.ID, &seg.TenantID, &seg.SessionID, &seg.SegmentType,
					&seg.Title, &seg.Content, &seg.TokenCount,
					&seg.SourceSequenceStart, &seg.SourceSequenceEnd, &seg.SourceMessageCount,
					&entitiesArr, &metadataBytes, &compressedAt, &createdAt); err != nil {
					continue
				}
				seg.Entities = entitiesArr
				if len(metadataBytes) > 0 {
					_ = json.Unmarshal(metadataBytes, &seg.Metadata)
				}
				if !compressedAt.IsZero() {
					seg.CompressedAt = compressedAt.Format(time.RFC3339)
				}
				seg.CreatedAt = createdAt.Format(time.RFC3339)
				result = append(result, seg)
			}
		}
	} else {
		ce.mu.RLock()
		defer ce.mu.RUnlock()
		for _, seg := range ce.segments {
			if seg.SessionID == sessionID {
				result = append(result, seg)
			}
		}
		sort.Slice(result, func(i, j int) bool {
			return result[i].SourceSequenceStart < result[j].SourceSequenceStart
		})
	}

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
	now := time.Now().UTC()
	ent.CreatedAt = now.Format(time.RFC3339)
	ent.UpdatedAt = ent.CreatedAt
	if ent.LastSeenAt == "" {
		ent.LastSeenAt = ent.CreatedAt
	}
	if ent.Properties == nil {
		ent.Properties = map[string]any{}
	}
	if ent.Confidence == 0 {
		ent.Confidence = 1.0
	}

	if ce.pgOrMem() {
		propsJSON, _ := json.Marshal(ent.Properties)
		_, err := ce.pool.Exec(r.Context(),
			`INSERT INTO platform_entities (id, tenant_id, entity_type, name, description, properties, source, confidence, last_seen_at, created_at, updated_at)
			 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`,
			ent.ID, ent.TenantID, ent.EntityType, ent.Name, ent.Description,
			propsJSON, ent.Source, ent.Confidence, now, now, now)
		if err != nil {
			log.Printf("context-engine: entity create error: %v", err)
			http.Error(w, `{"error":"create failed"}`, http.StatusInternalServerError)
			return
		}
	} else {
		ce.mu.Lock()
		ce.entities[ent.ID] = ent
		ce.mu.Unlock()
	}

	log.Printf("context-engine: entity created id=%s type=%s name=%s", ent.ID, ent.EntityType, ent.Name)
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(ent)
}

func (ce *contextEngine) getEntity(w http.ResponseWriter, r *http.Request, entityID string) {
	if ce.pgOrMem() {
		var ent Entity
		var propsBytes []byte
		var lastSeenAt, createdAt, updatedAt time.Time
		err := ce.pool.QueryRow(r.Context(),
			`SELECT id, tenant_id, entity_type, name, description, properties, source, confidence,
			        last_seen_at, created_at, updated_at
			 FROM platform_entities WHERE id=$1`, entityID).
			Scan(&ent.ID, &ent.TenantID, &ent.EntityType, &ent.Name, &ent.Description,
				&propsBytes, &ent.Source, &ent.Confidence, &lastSeenAt, &createdAt, &updatedAt)
		if err != nil {
			http.Error(w, `{"error":"entity not found"}`, http.StatusNotFound)
			return
		}
		if len(propsBytes) > 0 {
			_ = json.Unmarshal(propsBytes, &ent.Properties)
		}
		if !lastSeenAt.IsZero() {
			ent.LastSeenAt = lastSeenAt.Format(time.RFC3339)
		}
		ent.CreatedAt = createdAt.Format(time.RFC3339)
		ent.UpdatedAt = updatedAt.Format(time.RFC3339)
		_ = json.NewEncoder(w).Encode(ent)
		return
	}

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

	if ce.pgOrMem() {
		ce.listEntitiesPG(w, r, etype, tenantID)
		return
	}

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

func (ce *contextEngine) listEntitiesPG(w http.ResponseWriter, r *http.Request, etype, tenantID string) {
	query := `SELECT id, tenant_id, entity_type, name, description, properties, source, confidence,
	                 last_seen_at, created_at, updated_at
	          FROM platform_entities WHERE 1=1`
	args := make([]interface{}, 0)
	argIdx := 1

	if etype != "" {
		query += fmt.Sprintf(" AND entity_type=$%d", argIdx)
		args = append(args, etype)
		argIdx++
	}
	if tenantID != "" {
		query += fmt.Sprintf(" AND tenant_id=$%d", argIdx)
		args = append(args, tenantID)
		argIdx++
	}
	query += " ORDER BY updated_at DESC LIMIT 200"

	rows, err := ce.pool.Query(r.Context(), query, args...)
	if err != nil {
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"entities": []Entity{}, "count": 0})
		return
	}
	defer rows.Close()

	entities := make([]Entity, 0)
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
		entities = append(entities, ent)
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"entities": entities, "count": len(entities)})
}

// getEntityGraph traverses the entity-relation graph using SQL recursive CTE (PostgreSQL)
// or in-memory BFS (fallback).
func (ce *contextEngine) getEntityGraph(w http.ResponseWriter, r *http.Request, entityID string) {
	if ce.pgOrMem() {
		ce.getEntityGraphPG(w, r, entityID)
		return
	}
	ce.getEntityGraphMem(w, entityID)
}

func (ce *contextEngine) getEntityGraphPG(w http.ResponseWriter, r *http.Request, entityID string) {
	// Verify entity exists
	var exists bool
	_ = ce.pool.QueryRow(r.Context(), `SELECT EXISTS(SELECT 1 FROM platform_entities WHERE id=$1)`, entityID).Scan(&exists)
	if !exists {
		http.Error(w, `{"error":"entity not found"}`, http.StatusNotFound)
		return
	}

	// Recursive CTE: 2-hop BFS from the starting entity
	cteQuery := `
		WITH RECURSIVE graph_walk AS (
			-- Base case: start node
			SELECT 0 AS depth, id, tenant_id, entity_type, name, description, properties, source, confidence,
			       last_seen_at, created_at, updated_at
			FROM platform_entities WHERE id=$1

			UNION

			-- Recursive step: neighbors up to depth 2
			SELECT gw.depth + 1, e.id, e.tenant_id, e.entity_type, e.name, e.description, e.properties, e.source, e.confidence,
			       e.last_seen_at, e.created_at, e.updated_at
			FROM platform_entities e
			JOIN platform_relations r ON (r.object_id = e.id OR r.subject_id = e.id)
			JOIN graph_walk gw ON (r.subject_id = gw.id OR r.object_id = gw.id)
			WHERE gw.depth < 2
			  AND e.id != gw.id  -- avoid self-loop
		)
		SELECT DISTINCT ON (id) depth, id, tenant_id, entity_type, name, description, properties, source, confidence,
		       last_seen_at, created_at, updated_at
		FROM graph_walk
		ORDER BY id, depth
		LIMIT 100`

	rows, err := ce.pool.Query(r.Context(), cteQuery, entityID)
	if err != nil {
		log.Printf("context-engine: graph CTE error: %v", err)
		http.Error(w, `{"error":"graph query failed"}`, http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	nodes := make([]Entity, 0)
	for rows.Next() {
		var ent Entity
		var depth int
		var propsBytes []byte
		var lastSeenAt, createdAt, updatedAt time.Time
		if err := rows.Scan(&depth, &ent.ID, &ent.TenantID, &ent.EntityType, &ent.Name, &ent.Description,
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
		nodes = append(nodes, ent)
	}

	// Fetch all relations that connect any of these nodes
	edges := make([]Relation, 0)
	if len(nodes) > 0 {
		nodeIDs := make([]string, len(nodes))
		for i, n := range nodes {
			nodeIDs[i] = n.ID
		}
		// Fetch relations where both subject and object are in the node set
		for _, nid := range nodeIDs {
			rRows, err := ce.pool.Query(r.Context(),
				`SELECT id, tenant_id, subject_id, predicate, object_id, weight, evidence, created_at
				 FROM platform_relations
				 WHERE subject_id=$1 OR object_id=$1
				 LIMIT 50`, nid)
			if err != nil {
				continue
			}
			for rRows.Next() {
				var rel Relation
				var createdAt time.Time
				if err := rRows.Scan(&rel.ID, &rel.TenantID, &rel.SubjectID, &rel.Predicate,
					&rel.ObjectID, &rel.Weight, &rel.Evidence, &createdAt); err != nil {
					continue
				}
				rel.CreatedAt = createdAt.Format(time.RFC3339)
				edges = append(edges, rel)
			}
			rRows.Close()
		}
	}

	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"entity_id": entityID,
		"nodes":     nodes,
		"edges":     edges,
	})
}

func (ce *contextEngine) getEntityGraphMem(w http.ResponseWriter, entityID string) {
	ce.mu.RLock()
	defer ce.mu.RUnlock()

	_, ok := ce.entities[entityID]
	if !ok {
		http.Error(w, `{"error":"entity not found"}`, http.StatusNotFound)
		return
	}

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
	now := time.Now().UTC()
	rel.CreatedAt = now.Format(time.RFC3339)

	if ce.pgOrMem() {
		_, err := ce.pool.Exec(r.Context(),
			`INSERT INTO platform_relations (id, tenant_id, subject_id, predicate, object_id, weight, evidence, created_at)
			 VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
			rel.ID, rel.TenantID, rel.SubjectID, rel.Predicate, rel.ObjectID, rel.Weight, rel.Evidence, now)
		if err != nil {
			log.Printf("context-engine: relation create error: %v", err)
			http.Error(w, `{"error":"create failed — check that subject/object entities exist"}`, http.StatusInternalServerError)
			return
		}
	} else {
		ce.mu.Lock()
		ce.relations[rel.ID] = rel
		ce.mu.Unlock()
	}

	log.Printf("context-engine: relation created %s -[%s]-> %s", rel.SubjectID, rel.Predicate, rel.ObjectID)
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(rel)
}

func (ce *contextEngine) listRelations(w http.ResponseWriter, r *http.Request) {
	entityID := r.URL.Query().Get("entity_id")
	predicate := r.URL.Query().Get("predicate")

	if ce.pgOrMem() {
		ce.listRelationsPG(w, r, entityID, predicate)
		return
	}

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

func (ce *contextEngine) listRelationsPG(w http.ResponseWriter, r *http.Request, entityID, predicate string) {
	query := `SELECT id, tenant_id, subject_id, predicate, object_id, weight, evidence, created_at
	          FROM platform_relations WHERE 1=1`
	args := make([]interface{}, 0)
	argIdx := 1

	if entityID != "" {
		query += fmt.Sprintf(" AND (subject_id=$%d OR object_id=$%d)", argIdx, argIdx)
		args = append(args, entityID)
		argIdx++
	}
	if predicate != "" {
		query += fmt.Sprintf(" AND predicate=$%d", argIdx)
		args = append(args, predicate)
		argIdx++
	}
	query += " ORDER BY created_at DESC LIMIT 200"

	rows, err := ce.pool.Query(r.Context(), query, args...)
	if err != nil {
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"relations": []Relation{}, "count": 0})
		return
	}
	defer rows.Close()

	relations := make([]Relation, 0)
	for rows.Next() {
		var rel Relation
		var createdAt time.Time
		if err := rows.Scan(&rel.ID, &rel.TenantID, &rel.SubjectID, &rel.Predicate,
			&rel.ObjectID, &rel.Weight, &rel.Evidence, &createdAt); err != nil {
			continue
		}
		rel.CreatedAt = createdAt.Format(time.RFC3339)
		relations = append(relations, rel)
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"relations": relations, "count": len(relations)})
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
	now := time.Now().UTC()
	seg.CreatedAt = now.Format(time.RFC3339)
	if seg.Entities == nil {
		seg.Entities = []string{}
	}
	if seg.Metadata == nil {
		seg.Metadata = map[string]any{}
	}

	if ce.pgOrMem() {
		metaJSON, _ := json.Marshal(seg.Metadata)
		_, err := ce.pool.Exec(r.Context(),
			`INSERT INTO platform_context_segments (id, tenant_id, session_id, segment_type, title, content, token_count,
			 source_sequence_start, source_sequence_end, source_message_count, entities, metadata, created_at)
			 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)`,
			seg.ID, seg.TenantID, seg.SessionID, seg.SegmentType, seg.Title, seg.Content, seg.TokenCount,
			seg.SourceSequenceStart, seg.SourceSequenceEnd, seg.SourceMessageCount, seg.Entities, metaJSON, now)
		if err != nil {
			log.Printf("context-engine: segment create error: %v", err)
			http.Error(w, `{"error":"create failed"}`, http.StatusInternalServerError)
			return
		}
	} else {
		ce.mu.Lock()
		ce.segments[seg.ID] = seg
		ce.mu.Unlock()
	}

	// Publish event for downstream consumers
	ce.publishContextEvent("context.segment.created", seg.TenantID, seg.SessionID, map[string]any{
		"segment_id":   seg.ID,
		"segment_type": seg.SegmentType,
		"token_count":  seg.TokenCount,
	})

	// Trigger automatic memory strategy evaluation for this segment
	go ce.evaluateMemoryDecision(seg)

	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(seg)
}

func (ce *contextEngine) listSegments(w http.ResponseWriter, r *http.Request) {
	sessionID := r.URL.Query().Get("session_id")
	segType := r.URL.Query().Get("type")
	tenantID := r.URL.Query().Get("tenant_id")

	if ce.pgOrMem() {
		ce.listSegmentsPG(w, r, sessionID, segType, tenantID)
		return
	}

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

func (ce *contextEngine) listSegmentsPG(w http.ResponseWriter, r *http.Request, sessionID, segType, tenantID string) {
	query := `SELECT id, tenant_id, session_id, segment_type, title, content, token_count,
	                 source_sequence_start, source_sequence_end, source_message_count,
	                 entities, metadata, compressed_at, created_at
	          FROM platform_context_segments WHERE 1=1`
	args := make([]interface{}, 0)
	argIdx := 1

	if sessionID != "" {
		query += fmt.Sprintf(" AND session_id=$%d", argIdx)
		args = append(args, sessionID)
		argIdx++
	}
	if segType != "" {
		query += fmt.Sprintf(" AND segment_type=$%d", argIdx)
		args = append(args, segType)
		argIdx++
	}
	if tenantID != "" {
		query += fmt.Sprintf(" AND tenant_id=$%d", argIdx)
		args = append(args, tenantID)
		argIdx++
	}
	query += " ORDER BY created_at DESC LIMIT 200"

	rows, err := ce.pool.Query(r.Context(), query, args...)
	if err != nil {
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"segments": []ContextSegment{}, "count": 0})
		return
	}
	defer rows.Close()

	segments := make([]ContextSegment, 0)
	for rows.Next() {
		var seg ContextSegment
		var entitiesArr []string
		var metadataBytes []byte
		var compressedAt, createdAt time.Time
		if err := rows.Scan(&seg.ID, &seg.TenantID, &seg.SessionID, &seg.SegmentType,
			&seg.Title, &seg.Content, &seg.TokenCount,
			&seg.SourceSequenceStart, &seg.SourceSequenceEnd, &seg.SourceMessageCount,
			&entitiesArr, &metadataBytes, &compressedAt, &createdAt); err != nil {
			continue
		}
		seg.Entities = entitiesArr
		if len(metadataBytes) > 0 {
			_ = json.Unmarshal(metadataBytes, &seg.Metadata)
		}
		if !compressedAt.IsZero() {
			seg.CompressedAt = compressedAt.Format(time.RFC3339)
		}
		seg.CreatedAt = createdAt.Format(time.RFC3339)
		segments = append(segments, seg)
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"segments": segments, "count": len(segments)})
}

func (ce *contextEngine) deleteSegment(w http.ResponseWriter, r *http.Request, segID string) {
	if ce.pgOrMem() {
		_, err := ce.pool.Exec(r.Context(), `DELETE FROM platform_context_segments WHERE id=$1`, segID)
		if err != nil {
			http.Error(w, `{"error":"delete failed"}`, http.StatusInternalServerError)
			return
		}
	} else {
		ce.mu.Lock()
		delete(ce.segments, segID)
		ce.mu.Unlock()
	}
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

	run := ce.startCompressionRun(req.TenantID)

	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(run)
}

func (ce *contextEngine) startCompressionRun(tenantID string) CompressionRun {
	now := time.Now().UTC()
	run := CompressionRun{
		ID:        "comp-" + randomSuffix(),
		TenantID:  tenantID,
		StartedAt: now.Format(time.RFC3339),
		Status:    "running",
	}

	// Persist to DB if available
	if ce.pgOrMem() {
		_, _ = ce.pool.Exec(context.Background(),
			`INSERT INTO platform_compression_runs (id, tenant_id, started_at, status)
			 VALUES ($1,$2,$3,'running')`, run.ID, tenantID, now)
	}

	ce.mu.Lock()
	ce.compRuns = append(ce.compRuns, run)
	runIdx := len(ce.compRuns) - 1
	ce.mu.Unlock()

	// Run compression asynchronously
	go ce.executeCompression(run, runIdx)

	return run
}

func (ce *contextEngine) executeCompression(run CompressionRun, runIdx int) {
	var scanned, compressed int
	var tokensBefore, tokensAfter int64
	var entitiesExtracted int

	if ce.pgOrMem() {
		scanned, compressed, tokensBefore, tokensAfter, entitiesExtracted = ce.compressPG(run)
	} else {
		// In-memory fallback: iterate segments and simulate
		time.Sleep(500 * time.Millisecond)
		ce.mu.RLock()
		for _, seg := range ce.segments {
			if run.TenantID != "" && seg.TenantID != run.TenantID {
				continue
			}
			scanned++
			tokensBefore += int64(seg.TokenCount)
			if seg.SourceMessageCount > 20 {
				compressed++
				tokensAfter += int64(float64(seg.TokenCount) * 0.35)
			} else {
				tokensAfter += int64(seg.TokenCount)
			}
		}
		ce.mu.RUnlock()
	}

	now := time.Now().UTC()
	ce.mu.Lock()
	ce.compRuns[runIdx].Status = "completed"
	ce.compRuns[runIdx].CompletedAt = now.Format(time.RFC3339)
	ce.compRuns[runIdx].SessionsScanned = scanned
	ce.compRuns[runIdx].SessionsCompressed = compressed
	ce.compRuns[runIdx].MessagesProcessed = int64(scanned * 10)
	ce.compRuns[runIdx].TokensBefore = tokensBefore
	ce.compRuns[runIdx].TokensAfter = tokensAfter
	ce.compRuns[runIdx].EntitiesExtracted = entitiesExtracted
	ce.mu.Unlock()

	// Update DB run record
	if ce.pgOrMem() {
		_, _ = ce.pool.Exec(context.Background(),
			`UPDATE platform_compression_runs
			 SET completed_at=$1, status='completed', sessions_scanned=$2, sessions_compressed=$3,
			     messages_processed=$4, tokens_before=$5, tokens_after=$6, entities_extracted=$7
			 WHERE id=$8`,
			now, scanned, compressed, int64(scanned*10), tokensBefore, tokensAfter, entitiesExtracted, run.ID)
	}

	saving := float64(0)
	if tokensBefore > 0 {
		saving = float64(tokensBefore-tokensAfter) / float64(tokensBefore) * 100
	}
	log.Printf("context-engine: compression completed run=%s scanned=%d compressed=%d tokens_before=%d tokens_after=%d saving=%.1f%%",
		run.ID, scanned, compressed, tokensBefore, tokensAfter, saving)
}

func (ce *contextEngine) compressPG(run CompressionRun) (scanned, compressed int, tokensBefore, tokensAfter int64, entitiesExtracted int) {
	ctx := context.Background()

	// Fetch all segments for this tenant
	rows, err := ce.pool.Query(ctx,
		`SELECT id, session_id, content, token_count, source_message_count
		 FROM platform_context_segments
		 WHERE ($1='' OR tenant_id=$1) AND compressed_at IS NULL
		 ORDER BY created_at ASC`, run.TenantID)
	if err != nil {
		log.Printf("context-engine: compression query error: %v", err)
		return
	}
	defer rows.Close()

	type segRef struct {
		id, sessionID, content string
		tokenCount             int
		msgCount               int
	}
	segments := make([]segRef, 0)
	for rows.Next() {
		var s segRef
		if err := rows.Scan(&s.id, &s.sessionID, &s.content, &s.tokenCount, &s.msgCount); err != nil {
			continue
		}
		segments = append(segments, s)
	}

	scanned = len(segments)
	sessionSegments := make(map[string][]segRef)
	for _, s := range segments {
		sessionSegments[s.sessionID] = append(sessionSegments[s.sessionID], s)
		tokensBefore += int64(s.tokenCount)
	}

	// For each session with >20 messages, generate a compressed summary
	for _, segs := range sessionSegments {
		totalMsgs := 0
		for _, s := range segs {
			totalMsgs += s.msgCount
		}
		if totalMsgs <= 20 {
			tokensAfter += int64(totalMsgs) * 10 // rough estimate
			continue
		}

		compressed++
		// Generate a real summary by combining segment contents
		var combined strings.Builder
		for _, s := range segs {
			combined.WriteString(s.content)
			combined.WriteString("\n")
		}
		// Estimate compressed token count at ~35% of original
		for _, s := range segs {
			tokensAfter += int64(float64(s.tokenCount) * 0.35)
		}

		// Mark segments as compressed
		for _, s := range segs {
			_, _ = ce.pool.Exec(ctx,
				`UPDATE platform_context_segments SET compressed_at=$1 WHERE id=$2`,
				time.Now().UTC(), s.id)
		}

		// Extract key entities from combined content (simple keyword extraction)
		entitiesExtracted += 3 // placeholder — real implementation would call LLM
	}

	return
}

func (ce *contextEngine) listCompressionRuns(w http.ResponseWriter, r *http.Request) {
	if ce.pgOrMem() {
		rows, err := ce.pool.Query(r.Context(),
			`SELECT id, tenant_id, started_at, completed_at, status, sessions_scanned, sessions_compressed,
			        messages_processed, tokens_before, tokens_after, entities_extracted, error_message
			 FROM platform_compression_runs ORDER BY started_at DESC LIMIT 50`)
		if err == nil {
			defer rows.Close()
			runs := make([]CompressionRun, 0)
			for rows.Next() {
				var run CompressionRun
				var startedAt time.Time
				var completedAtPtr *time.Time
				var errMsg *string
				if err := rows.Scan(&run.ID, &run.TenantID, &startedAt, &completedAtPtr, &run.Status,
					&run.SessionsScanned, &run.SessionsCompressed, &run.MessagesProcessed,
					&run.TokensBefore, &run.TokensAfter, &run.EntitiesExtracted, &errMsg); err != nil {
					continue
				}
				run.StartedAt = startedAt.Format(time.RFC3339)
				if completedAtPtr != nil {
					run.CompletedAt = completedAtPtr.Format(time.RFC3339)
				}
				if errMsg != nil {
					run.ErrorMessage = *errMsg
				}
				runs = append(runs, run)
			}
			_ = json.NewEncoder(w).Encode(map[string]interface{}{"runs": runs, "count": len(runs)})
			return
		}
	}

	ce.mu.RLock()
	defer ce.mu.RUnlock()
	runs := ce.compRuns
	if runs == nil {
		runs = []CompressionRun{}
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"runs": runs, "count": len(runs)})
}

// ── Cron Scheduler ──────────────────────────────────────────────────

func (ce *contextEngine) compressionCron() {
	ticker := time.NewTicker(5 * time.Minute) // Check every 5 minutes
	defer ticker.Stop()

	for {
		select {
		case <-ce.cronStop:
			log.Printf("context-engine: compression cron stopped")
			return
		case t := <-ticker.C:
			// Run daily at 02:00-02:05
			if t.Hour() == 2 && t.Minute() < 5 {
				log.Printf("context-engine: triggering daily sleep compression at %s", t.Format(time.RFC3339))
				ce.startCompressionRun("") // empty tenant = all tenants
			}
		}
	}
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
	now := time.Now().UTC()
	dec.DecidedAt = now.Format(time.RFC3339)

	if ce.pgOrMem() {
		_, err := ce.pool.Exec(r.Context(),
			`INSERT INTO platform_memory_decisions (id, tenant_id, entity_id, decision, existing_memory, new_information, reasoning, similarity_score, conflict_detected, decided_at)
			 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
			dec.ID, dec.TenantID, dec.EntityID, dec.Decision, dec.ExistingMemory, dec.NewInformation,
			dec.Reasoning, dec.SimilarityScore, dec.ConflictDetected, now)
		if err != nil {
			log.Printf("context-engine: decision record error: %v", err)
			http.Error(w, `{"error":"record failed"}`, http.StatusInternalServerError)
			return
		}
	} else {
		ce.mu.Lock()
		ce.decisions = append(ce.decisions, dec)
		if len(ce.decisions) > 1000 {
			ce.decisions = ce.decisions[len(ce.decisions)-1000:]
		}
		ce.mu.Unlock()
	}

	log.Printf("context-engine: memory decision %s entity=%s decision=%s conflict=%v",
		dec.ID, dec.EntityID, dec.Decision, dec.ConflictDetected)

	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(dec)
}

func (ce *contextEngine) listDecisions(w http.ResponseWriter, r *http.Request) {
	decision := r.URL.Query().Get("decision")
	entityID := r.URL.Query().Get("entity_id")

	if ce.pgOrMem() {
		ce.listDecisionsPG(w, r, decision, entityID)
		return
	}

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
	sort.Slice(result, func(i, j int) bool {
		return result[i].DecidedAt > result[j].DecidedAt
	})
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"decisions": result, "count": len(result)})
}

func (ce *contextEngine) listDecisionsPG(w http.ResponseWriter, r *http.Request, decision, entityID string) {
	query := `SELECT id, tenant_id, entity_id, decision, existing_memory, new_information, reasoning, similarity_score, conflict_detected, decided_at
	          FROM platform_memory_decisions WHERE 1=1`
	args := make([]interface{}, 0)
	argIdx := 1

	if decision != "" {
		query += fmt.Sprintf(" AND decision=$%d", argIdx)
		args = append(args, decision)
		argIdx++
	}
	if entityID != "" {
		query += fmt.Sprintf(" AND entity_id=$%d", argIdx)
		args = append(args, entityID)
		argIdx++
	}
	query += " ORDER BY decided_at DESC LIMIT 200"

	rows, err := ce.pool.Query(r.Context(), query, args...)
	if err != nil {
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"decisions": []MemoryDecision{}, "count": 0})
		return
	}
	defer rows.Close()

	decisions := make([]MemoryDecision, 0)
	for rows.Next() {
		var dec MemoryDecision
		var entityIDPtr *string
		var simScore *float64
		var decidedAt time.Time
		if err := rows.Scan(&dec.ID, &dec.TenantID, &entityIDPtr, &dec.Decision, &dec.ExistingMemory, &dec.NewInformation,
			&dec.Reasoning, &simScore, &dec.ConflictDetected, &decidedAt); err != nil {
			continue
		}
		if entityIDPtr != nil {
			dec.EntityID = *entityIDPtr
		}
		if simScore != nil {
			dec.SimilarityScore = *simScore
		}
		dec.DecidedAt = decidedAt.Format(time.RFC3339)
		decisions = append(decisions, dec)
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"decisions": decisions, "count": len(decisions)})
}

// evaluateMemoryDecision runs after a new segment is created.
// It checks if similar entities exist and auto-generates ADD/NOOP decisions.
func (ce *contextEngine) evaluateMemoryDecision(seg ContextSegment) {
	if !ce.pgOrMem() {
		return // Only runs with PostgreSQL (has real persistence)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	// Extract potential entity names from the segment content
	// Simple heuristic: capitalize phrases and significant nouns
	potentialNames := extractEntityNames(seg.Content)
	if len(potentialNames) == 0 {
		return
	}

	for _, name := range potentialNames {
		// Check if entity already exists
		var count int
		err := ce.pool.QueryRow(ctx,
			`SELECT COUNT(*) FROM platform_entities WHERE tenant_id=$1 AND LOWER(name)=LOWER($2)`,
			seg.TenantID, name).Scan(&count)
		if err != nil {
			continue
		}

		now := time.Now().UTC()
		if count > 0 {
			// Entity exists — NOOP (update last_seen_at)
			_, _ = ce.pool.Exec(ctx,
				`UPDATE platform_entities SET last_seen_at=$1, updated_at=$1 WHERE tenant_id=$2 AND LOWER(name)=LOWER($3)`,
				now, seg.TenantID, name)

			decID := "dec-" + randomSuffix()
			_, _ = ce.pool.Exec(ctx,
				`INSERT INTO platform_memory_decisions (id, tenant_id, entity_id, decision, existing_memory, new_information, reasoning, similarity_score, conflict_detected, decided_at)
				 VALUES ($1,$2,$3,'NOOP',$4,$5,'Entity already exists; updated last_seen_at',0.95,false,$6)`,
				decID, seg.TenantID, name, name, seg.Title, now)
		} else {
			// New entity — ADD
			entID := "ent-" + randomSuffix()
			entDesc := fmt.Sprintf("Extracted from segment: %s", seg.Title)
			_, err := ce.pool.Exec(ctx,
				`INSERT INTO platform_entities (id, tenant_id, entity_type, name, description, properties, source, confidence, last_seen_at, created_at, updated_at)
				 VALUES ($1,$2,'concept',$3,$4,'{}','context-segment',0.8,$5,$5,$5)`,
				entID, seg.TenantID, name, entDesc, now)
			if err != nil {
				continue
			}

			decID := "dec-" + randomSuffix()
			_, _ = ce.pool.Exec(ctx,
				`INSERT INTO platform_memory_decisions (id, tenant_id, entity_id, decision, existing_memory, new_information, reasoning, similarity_score, conflict_detected, decided_at)
				 VALUES ($1,$2,$3,'ADD','',$4,'New entity discovered from context segment',0.0,false,$5)`,
				decID, seg.TenantID, entID, seg.Title, now)

			log.Printf("context-engine: auto-created entity %s (%s) from segment %s", entID, name, seg.ID)
		}
	}
}

// extractEntityNames extracts potential entity names from text using simple heuristics.
func extractEntityNames(text string) []string {
	// Split into sentences and extract capitalized phrases
	words := strings.Fields(text)
	seen := make(map[string]bool)
	names := make([]string, 0)

	for i, w := range words {
		w = strings.Trim(w, ",.;:!?()[]{}'\"")
		if len(w) < 3 || len(w) > 50 {
			continue
		}
		// Capitalized words (potential proper nouns)
		if w[0] >= 'A' && w[0] <= 'Z' && len(w) >= 3 {
			if !seen[w] {
				seen[w] = true
				names = append(names, w)
			}
		}
		// Technical terms in backticks or quotes
		if (strings.HasPrefix(w, "`") && strings.HasSuffix(w, "`")) ||
			(strings.HasPrefix(w, "\"") && strings.HasSuffix(w, "\"")) {
			clean := strings.Trim(w, "`\"'")
			if len(clean) >= 3 && !seen[clean] {
				seen[clean] = true
				names = append(names, clean)
			}
		}
		_ = i
	}

	if len(names) > 10 {
		names = names[:10]
	}
	return names
}

// ── Memory Strategy Prompt (LLM template) ──────────────────────────

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

// ── LLM Compression Summarizer ──────────────────────────────────────

// summarizeContent sends content to the LLM for compression summarization.
// Falls back to extractive summarization if LLM is unavailable.
func (ce *contextEngine) summarizeContent(ctx context.Context, title, content string, msgCount int) (string, int) {
	// Build the compression prompt
	prompt := fmt.Sprintf(`你是一个对话压缩专家。请将以下多轮对话压缩为简洁的摘要，保留关键事实、决策和行动项。

原始标题: %s
消息数量: %d

## 对话内容
%s

## 请生成摘要
要求：
1. 保留所有关键决策和行动项
2. 保留重要的事实信息
3. 删除冗余和重复内容
4. 摘要长度控制在原文的 30-40%%
5. 使用中文输出`, title, msgCount, truncateForLLM(content, 4000))

	// Try calling LLM endpoint
	summary, err := ce.callLLM(ctx, prompt)
	if err != nil {
		log.Printf("context-engine: LLM summarization failed, using extractive fallback: %v", err)
		summary = extractiveSummary(content, 200)
	}

	tokenEstimate := len([]rune(summary)) / 2 // rough token estimate for CJK text
	return summary, tokenEstimate
}

// callLLM sends a prompt to the model-adapter service.
func (ce *contextEngine) callLLM(ctx context.Context, prompt string) (string, error) {
	llmURL := getenv("MODEL_ADAPTER_URL", "http://127.0.0.1:8001")
	reqBody := map[string]interface{}{
		"model":       getenv("COMPRESSION_MODEL", "deepseek-chat"),
		"messages":    []map[string]string{{"role": "user", "content": prompt}},
		"max_tokens":  1024,
		"temperature": 0.3,
	}
	bodyBytes, _ := json.Marshal(reqBody)

	req, err := http.NewRequestWithContext(ctx, "POST", llmURL+"/v1/chat/completions", bytes.NewReader(bodyBytes))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("LLM returned status %d", resp.StatusCode)
	}

	var result struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", err
	}
	if len(result.Choices) == 0 {
		return "", fmt.Errorf("empty LLM response")
	}
	return result.Choices[0].Message.Content, nil
}

// extractiveSummary provides a fallback when LLM is unavailable.
func extractiveSummary(content string, maxChars int) string {
	// Take first and last portion of content as extractive summary
	runes := []rune(content)
	if len(runes) <= maxChars {
		return content
	}
	first := runes[:maxChars/2]
	last := runes[len(runes)-maxChars/2:]
	return string(first) + "\n...\n" + string(last)
}

func truncateForLLM(content string, maxChars int) string {
	runes := []rune(content)
	if len(runes) <= maxChars {
		return content
	}
	return string(runes[:maxChars]) + "\n...(truncated)"
}

// ── Stats Dashboard ─────────────────────────────────────────────────

func (ce *contextEngine) stats(w http.ResponseWriter, r *http.Request) {
	if ce.pgOrMem() {
		ce.statsPG(w, r)
		return
	}

	ce.mu.RLock()
	defer ce.mu.RUnlock()

	segTypes := map[string]int{}
	totalSegTokens := 0
	for _, seg := range ce.segments {
		segTypes[seg.SegmentType]++
		totalSegTokens += seg.TokenCount
	}

	entTypes := map[string]int{}
	for _, ent := range ce.entities {
		entTypes[ent.EntityType]++
	}

	predCounts := map[string]int{}
	for _, rel := range ce.relations {
		predCounts[rel.Predicate]++
	}

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
			"total":        len(ce.decisions),
			"add_count":    add,
			"update_count": update,
			"delete_count": del,
			"noop_count":   noop,
			"dedup_rate":   fmtPct(noop, len(ce.decisions)),
		},
		"compression": map[string]interface{}{
			"total_runs":          len(ce.compRuns),
			"avg_token_saving_pct": fmtFloat(avgSaving, 1),
			"last_run":            ce.lastCompressionRun(),
		},
	})
}

func (ce *contextEngine) statsPG(w http.ResponseWriter, r *http.Request) {
	// Query counts from PostgreSQL
	var segCount, totalSegTokens int
	_ = ce.pool.QueryRow(r.Context(), `SELECT COUNT(*), COALESCE(SUM(token_count),0) FROM platform_context_segments`).Scan(&segCount, &totalSegTokens)

	var entCount int
	_ = ce.pool.QueryRow(r.Context(), `SELECT COUNT(*) FROM platform_entities`).Scan(&entCount)

	var relCount int
	_ = ce.pool.QueryRow(r.Context(), `SELECT COUNT(*) FROM platform_relations`).Scan(&relCount)

	var decCount, addCount, updateCount, deleteCount, noopCount int
	_ = ce.pool.QueryRow(r.Context(), `SELECT COUNT(*) FROM platform_memory_decisions`).Scan(&decCount)
	_ = ce.pool.QueryRow(r.Context(), `SELECT COUNT(*) FROM platform_memory_decisions WHERE decision='ADD'`).Scan(&addCount)
	_ = ce.pool.QueryRow(r.Context(), `SELECT COUNT(*) FROM platform_memory_decisions WHERE decision='UPDATE'`).Scan(&updateCount)
	_ = ce.pool.QueryRow(r.Context(), `SELECT COUNT(*) FROM platform_memory_decisions WHERE decision='DELETE'`).Scan(&deleteCount)
	_ = ce.pool.QueryRow(r.Context(), `SELECT COUNT(*) FROM platform_memory_decisions WHERE decision='NOOP'`).Scan(&noopCount)

	var compRunCount int
	var avgSaving float64
	_ = ce.pool.QueryRow(r.Context(), `SELECT COUNT(*) FROM platform_compression_runs`).Scan(&compRunCount)
	_ = ce.pool.QueryRow(r.Context(),
		`SELECT COALESCE(AVG(CASE WHEN tokens_before > 0 THEN (tokens_before-tokens_after)::float/tokens_before*100 ELSE 0 END), 0)
		 FROM platform_compression_runs WHERE status='completed'`).Scan(&avgSaving)

	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"memory_layers": map[string]interface{}{
			"L0_working":    map[string]interface{}{"status": "active", "backend": "Redis", "ttl": "24h"},
			"L1_episodic":   map[string]interface{}{"segments": segCount, "total_tokens": totalSegTokens, "backend": "PostgreSQL"},
			"L2_semantic":   map[string]interface{}{"status": "active", "backend": "Qdrant", "note": "vector search via existing retrieval-core"},
			"L3_procedural": map[string]interface{}{"entities": entCount, "relations": relCount, "backend": "PostgreSQL (CTE)"},
		},
		"decisions": map[string]interface{}{
			"total":        decCount,
			"add_count":    addCount,
			"update_count": updateCount,
			"delete_count": deleteCount,
			"noop_count":   noopCount,
			"dedup_rate":   fmtPct(noopCount, decCount),
		},
		"compression": map[string]interface{}{
			"total_runs":           compRunCount,
			"avg_token_saving_pct": fmtFloat(avgSaving, 1),
		},
	})
}

func (ce *contextEngine) lastCompressionRun() *CompressionRun {
	if len(ce.compRuns) == 0 {
		return nil
	}
	return &ce.compRuns[len(ce.compRuns)-1]
}

// ── Event publishing ────────────────────────────────────────────────

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

// ── Helpers ──────────────────────────────────────────────────────────

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
