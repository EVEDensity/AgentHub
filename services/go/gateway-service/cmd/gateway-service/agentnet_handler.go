package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
)

// ── AgentNet data types ────────────────────────────────────────────────

// AgentCapability is a self-declared capability manifest published by each Agent.
type AgentCapability struct {
	AgentID        string   `json:"agent_id"`
	DisplayName    string   `json:"display_name"`
	Capabilities   []string `json:"capabilities"`
	PreferredTools []string `json:"preferred_tools"`
	QualityScore   float64  `json:"quality_score"`
	CurrentLoad    int      `json:"current_load"`
	MaxConcurrent  int      `json:"max_concurrent"`
	CostPerTask    float64  `json:"cost_per_task"`
	Status         string   `json:"status"` // idle, busy, overloaded, offline
	LastHeartbeat  string   `json:"last_heartbeat"`
	RegisteredAt   string   `json:"registered_at"`
}

// AgentNetTask represents a task published to the agent network.
type AgentNetTask struct {
	TaskID         string     `json:"task_id"`
	ParentTaskID   string     `json:"parent_task_id,omitempty"`
	DAGID          string     `json:"dag_id,omitempty"`
	CorrelationID  string     `json:"correlation_id"`
	Category       string     `json:"category"`
	Description    string     `json:"description"`
	RequiredCap    string     `json:"required_capability"`
	AssignedAgent  string     `json:"assigned_agent,omitempty"`
	Status         string     `json:"status"` // pending, assigned, running, completed, failed
	Input          any        `json:"input,omitempty"`
	Result         any        `json:"result,omitempty"`
	Error          string     `json:"error,omitempty"`
	CreatedAt      string     `json:"created_at"`
	AssignedAt     string     `json:"assigned_at,omitempty"`
	CompletedAt    string     `json:"completed_at,omitempty"`
}

// DAGNode is a node in the dynamic DAG graph.
type DAGNode struct {
	ID            string   `json:"id"`
	TaskID        string   `json:"task_id,omitempty"`
	AgentID       string   `json:"agent_id,omitempty"`
	Description   string   `json:"description"`
	RequiredCap   string   `json:"required_capability"`
	Dependencies  []string `json:"dependencies"`
	Status        string   `json:"status"` // pending, ready, running, completed, failed
	Priority      int      `json:"priority"`
	EstimatedSecs int      `json:"estimated_seconds"`
	Result        any      `json:"result,omitempty"`
	Error         string   `json:"error,omitempty"`
	StartedAt     string   `json:"started_at,omitempty"`
	CompletedAt   string   `json:"completed_at,omitempty"`
}

// DAGEdge represents a directed edge between two DAG nodes.
type DAGEdge struct {
	From      string  `json:"from"`
	To        string  `json:"to"`
	Label     string  `json:"label,omitempty"`
	Weight    float64 `json:"weight"`
}

// DAG represents a dynamic task graph.
type DAG struct {
	DAGID     string    `json:"dag_id"`
	Name      string    `json:"name"`
	TenantID  string    `json:"tenant_id"`
	SessionID string    `json:"session_id"`
	Nodes     []DAGNode `json:"nodes"`
	Edges     []DAGEdge `json:"edges"`
	Status    string    `json:"status"` // created, running, completed, failed, cancelled
	Strategy  string    `json:"strategy"` // round-robin, least-loaded, capability-match, cost-optimized
	CreatedAt string    `json:"created_at"`
	UpdatedAt string    `json:"updated_at"`
}

// AgentSpawn represents a child agent spawned by a parent.
type AgentSpawn struct {
	SpawnID      string `json:"spawn_id"`
	ParentID     string `json:"parent_id"`
	ChildID      string `json:"child_id"`
	ChildName    string `json:"child_name"`
	Reason       string `json:"reason"`
	Capabilities []string `json:"capabilities"`
	Status       string `json:"status"` // created, running, completed, destroyed
	CreatedAt    string `json:"created_at"`
	CompletedAt  string `json:"completed_at,omitempty"`
	TTLSeconds   int    `json:"ttl_seconds"`
}

// SharedMemoryEntry is a message written to the shared agent memory channel.
type SharedMemoryEntry struct {
	ID        string `json:"id"`
	AgentID   string `json:"agent_id"`
	Content   string `json:"content"`
	Intent    string `json:"intent,omitempty"`
	Target    string `json:"target,omitempty"`
	Timestamp string `json:"timestamp"`
}

// AgentNetStats provides overview statistics for the monitoring dashboard.
type AgentNetStats struct {
	TotalAgents     int            `json:"total_agents"`
	ActiveAgents    int            `json:"active_agents"`
	AgentByStatus   map[string]int `json:"agents_by_status"`
	TotalTasks      int            `json:"total_tasks"`
	TasksByStatus   map[string]int `json:"tasks_by_status"`
	ActiveDAGs      int            `json:"active_dags"`
	ActiveSpawns    int            `json:"active_spawns"`
	MemoryEntries   int            `json:"memory_entries"`
	AvgQualityScore float64        `json:"avg_quality_score"`
}

// ── AgentNet handler ───────────────────────────────────────────────────

// agentNetHandler implements the AgentNet REST API as an http.Handler.
type agentNetHandler struct {
	mu sync.RWMutex

	capabilities map[string]*AgentCapability // keyed by agent_id
	tasks        map[string]*AgentNetTask    // keyed by task_id
	dags         map[string]*DAG             // keyed by dag_id
	spawns       map[string]*AgentSpawn      // keyed by spawn_id
	memories     []SharedMemoryEntry

	bus      *eventbus.Client
	instance string
}

func newAgentNetHandler(bus *eventbus.Client) *agentNetHandler {
	return &agentNetHandler{
		capabilities: make(map[string]*AgentCapability),
		tasks:        make(map[string]*AgentNetTask),
		dags:         make(map[string]*DAG),
		spawns:       make(map[string]*AgentSpawn),
		memories:     make([]SharedMemoryEntry, 0),
		bus:          bus,
		instance:     getenv("HOSTNAME", "local"),
	}
}

// randomSuffix is defined in workspace_handler.go — reused here.

func (h *agentNetHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}

	rel := strings.TrimPrefix(r.URL.Path, "/agentnet")
	rel = strings.TrimPrefix(rel, "/")

	// Route: /agentnet/capabilities
	if rel == "capabilities" || strings.HasPrefix(rel, "capabilities/") {
		h.serveCapabilities(w, r, strings.TrimPrefix(rel, "capabilities/"))
		return
	}

	// Route: /agentnet/discover
	if strings.HasPrefix(rel, "discover") {
		h.serveDiscover(w, r)
		return
	}

	// Route: /agentnet/tasks
	if rel == "tasks" || strings.HasPrefix(rel, "tasks/") {
		h.serveTasks(w, r, strings.TrimPrefix(rel, "tasks/"))
		return
	}

	// Route: /agentnet/dag
	if rel == "dag" || strings.HasPrefix(rel, "dag/") {
		h.serveDAG(w, r, strings.TrimPrefix(rel, "dag/"))
		return
	}

	// Route: /agentnet/spawn
	if rel == "spawn" || strings.HasPrefix(rel, "spawn/") {
		h.serveSpawn(w, r, strings.TrimPrefix(rel, "spawn/"))
		return
	}

	// Route: /agentnet/memory
	if rel == "memory" || strings.HasPrefix(rel, "memory/") {
		h.serveMemory(w, r)
		return
	}

	// Route: /agentnet/stats
	if rel == "stats" {
		h.serveStats(w, r)
		return
	}

	// Route: /agentnet/topology
	if rel == "topology" {
		h.serveTopology(w, r)
		return
	}

	// Route: /agentnet/heartbeat/{agent_id}
	if strings.HasPrefix(rel, "heartbeat/") {
		h.serveHeartbeat(w, r, strings.TrimPrefix(rel, "heartbeat/"))
		return
	}

	http.NotFound(w, r)
}

// ── I1: Capability Manifest ────────────────────────────────────────────

func (h *agentNetHandler) serveCapabilities(w http.ResponseWriter, r *http.Request, subPath string) {
	w.Header().Set("Content-Type", "application/json")

	switch r.Method {
	case http.MethodPost:
		// POST /agentnet/capabilities — register or update manifest
		var cap AgentCapability
		if err := json.NewDecoder(r.Body).Decode(&cap); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "invalid json: " + err.Error()})
			return
		}
		if cap.AgentID == "" {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "agent_id is required"})
			return
		}
		if cap.Status == "" {
			cap.Status = "idle"
		}
		if cap.MaxConcurrent == 0 {
			cap.MaxConcurrent = 5
		}
		now := time.Now().UTC().Format(time.RFC3339)
		if cap.RegisteredAt == "" {
			cap.RegisteredAt = now
		}
		cap.LastHeartbeat = now

		h.mu.Lock()
		h.capabilities[cap.AgentID] = &cap
		h.mu.Unlock()

		// Publish capability announcement to NATS
		h.publishAgentNetEvent(events.EventAgentCapabilityAnnounced, "", "", map[string]any{
			"agent_id":      cap.AgentID,
			"capabilities":  cap.Capabilities,
			"quality_score": cap.QualityScore,
			"status":        cap.Status,
		})

		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(cap)

	case http.MethodGet:
		if subPath == "" {
			// GET /agentnet/capabilities — list all
			h.mu.RLock()
			list := make([]*AgentCapability, 0, len(h.capabilities))
			for _, c := range h.capabilities {
				list = append(list, c)
			}
			h.mu.RUnlock()

			// Optional filter by capability
			filter := r.URL.Query().Get("capability")
			if filter != "" {
				filtered := make([]*AgentCapability, 0)
				for _, c := range list {
					for _, cap := range c.Capabilities {
						if strings.Contains(strings.ToLower(cap), strings.ToLower(filter)) {
							filtered = append(filtered, c)
							break
						}
					}
				}
				list = filtered
			}

			sort.Slice(list, func(i, j int) bool {
				return list[i].QualityScore > list[j].QualityScore
			})

			json.NewEncoder(w).Encode(list)
			return
		}

		// GET /agentnet/capabilities/{agent_id} — get specific
		h.mu.RLock()
		cap, ok := h.capabilities[subPath]
		h.mu.RUnlock()
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "agent not found"})
			return
		}
		json.NewEncoder(w).Encode(cap)

	case http.MethodDelete:
		// DELETE /agentnet/capabilities/{agent_id} — unregister
		h.mu.Lock()
		delete(h.capabilities, subPath)
		h.mu.Unlock()
		w.WriteHeader(http.StatusNoContent)

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// ── I1: Agent Discovery ────────────────────────────────────────────────

func (h *agentNetHandler) serveDiscover(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	requiredCap := r.URL.Query().Get("capability")
	strategy := r.URL.Query().Get("strategy")
	if strategy == "" {
		strategy = "capability-match"
	}

	h.mu.RLock()
	defer h.mu.RUnlock()

	// Find agents matching the required capability
	var candidates []*AgentCapability
	for _, c := range h.capabilities {
		if c.Status == "offline" {
			continue
		}
		for _, cap := range c.Capabilities {
			if requiredCap == "" || strings.Contains(strings.ToLower(cap), strings.ToLower(requiredCap)) {
				candidates = append(candidates, c)
				break
			}
		}
	}

	if len(candidates) == 0 {
		json.NewEncoder(w).Encode(map[string]any{
			"candidates": []*AgentCapability{},
			"strategy":   strategy,
			"message":    "no matching agents found",
		})
		return
	}

	// Apply selection strategy
	var selected *AgentCapability
	switch strategy {
	case "least-loaded":
		sort.Slice(candidates, func(i, j int) bool {
			li := float64(candidates[i].CurrentLoad) / float64(candidates[i].MaxConcurrent)
			lj := float64(candidates[j].CurrentLoad) / float64(candidates[j].MaxConcurrent)
			return li < lj
		})
		selected = candidates[0]

	case "round-robin":
		// Simple: pick first available (production would track round-robin state)
		sort.Slice(candidates, func(i, j int) bool {
			return candidates[i].LastHeartbeat > candidates[j].LastHeartbeat
		})
		selected = candidates[0]

	case "cost-optimized":
		sort.Slice(candidates, func(i, j int) bool {
			si := candidates[i].QualityScore / max(candidates[i].CostPerTask, 0.01)
			sj := candidates[j].QualityScore / max(candidates[j].CostPerTask, 0.01)
			return si > sj
		})
		selected = candidates[0]

	case "capability-match":
		fallthrough
	default:
		sort.Slice(candidates, func(i, j int) bool {
			return candidates[i].QualityScore > candidates[j].QualityScore
		})
		selected = candidates[0]
	}

	json.NewEncoder(w).Encode(map[string]any{
		"candidates": candidates,
		"selected":   selected,
		"strategy":   strategy,
	})
}

// ── I2: Task Management ────────────────────────────────────────────────

func (h *agentNetHandler) serveTasks(w http.ResponseWriter, r *http.Request, subPath string) {
	w.Header().Set("Content-Type", "application/json")

	switch r.Method {
	case http.MethodPost:
		if subPath == "" {
			// POST /agentnet/tasks — publish a new task
			var task AgentNetTask
			if err := json.NewDecoder(r.Body).Decode(&task); err != nil {
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "invalid json: " + err.Error()})
				return
			}
			if task.TaskID == "" {
				task.TaskID = "task-" + randomSuffix()
			}
			if task.CorrelationID == "" {
				task.CorrelationID = task.TaskID
			}
			task.Status = "pending"
			task.CreatedAt = time.Now().UTC().Format(time.RFC3339)

			h.mu.Lock()
			h.tasks[task.TaskID] = &task
			h.mu.Unlock()

			// Publish to NATS for agent discovery
			h.publishAgentNetEvent(events.EventAgentTaskPublished, "", "", map[string]any{
				"task_id":             task.TaskID,
				"category":            task.Category,
				"required_capability": task.RequiredCap,
				"correlation_id":      task.CorrelationID,
				"dag_id":              task.DAGID,
			})

			w.WriteHeader(http.StatusCreated)
			json.NewEncoder(w).Encode(task)
			return
		}

		// POST /agentnet/tasks/{task_id}/result — report result
		parts := strings.SplitN(subPath, "/", 3)
		if len(parts) == 2 && parts[1] == "result" {
			taskID := parts[0]
			h.mu.Lock()
			task, ok := h.tasks[taskID]
			if !ok {
				h.mu.Unlock()
				w.WriteHeader(http.StatusNotFound)
				json.NewEncoder(w).Encode(map[string]string{"error": "task not found"})
				return
			}
			var result struct {
				AgentID string `json:"agent_id"`
				Result  any    `json:"result"`
				Error   string `json:"error,omitempty"`
			}
			if err := json.NewDecoder(r.Body).Decode(&result); err != nil {
				h.mu.Unlock()
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
				return
			}
			now := time.Now().UTC().Format(time.RFC3339)
			task.CompletedAt = now
			task.Result = result.Result
			task.Error = result.Error
			if result.Error != "" {
				task.Status = "failed"
			} else {
				task.Status = "completed"
			}
			h.mu.Unlock()

			h.publishAgentNetEvent(events.EventAgentTaskCompleted, "", "", map[string]any{
				"task_id":   taskID,
				"agent_id":  result.AgentID,
				"status":    task.Status,
				"error":     result.Error,
			})

			json.NewEncoder(w).Encode(task)
			return
		}
		http.NotFound(w, r)

	case http.MethodGet:
		if subPath == "" {
			// GET /agentnet/tasks — list all tasks
			statusFilter := r.URL.Query().Get("status")
			dagFilter := r.URL.Query().Get("dag_id")

			h.mu.RLock()
			list := make([]*AgentNetTask, 0, len(h.tasks))
			for _, t := range h.tasks {
				if statusFilter != "" && t.Status != statusFilter {
					continue
				}
				if dagFilter != "" && t.DAGID != dagFilter {
					continue
				}
				list = append(list, t)
			}
			h.mu.RUnlock()

			sort.Slice(list, func(i, j int) bool {
				return list[i].CreatedAt > list[j].CreatedAt
			})

			json.NewEncoder(w).Encode(list)
			return
		}

		// GET /agentnet/tasks/{task_id} — get specific task
		h.mu.RLock()
		task, ok := h.tasks[subPath]
		h.mu.RUnlock()
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "task not found"})
			return
		}
		json.NewEncoder(w).Encode(task)

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// ── I3: Dynamic DAG Engine ─────────────────────────────────────────────

func (h *agentNetHandler) serveDAG(w http.ResponseWriter, r *http.Request, subPath string) {
	w.Header().Set("Content-Type", "application/json")

	switch r.Method {
	case http.MethodPost:
		// POST /agentnet/dag — create new DAG
		var dag DAG
		if err := json.NewDecoder(r.Body).Decode(&dag); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "invalid json: " + err.Error()})
			return
		}
		if dag.DAGID == "" {
			dag.DAGID = "dag-" + randomSuffix()
		}
		if dag.Strategy == "" {
			dag.Strategy = "capability-match"
		}
		dag.Status = "created"
		now := time.Now().UTC().Format(time.RFC3339)
		dag.CreatedAt = now
		dag.UpdatedAt = now

		// Initialize all nodes as pending
		for i := range dag.Nodes {
			if dag.Nodes[i].Status == "" {
				dag.Nodes[i].Status = "pending"
			}
		}
		if dag.Edges == nil {
			dag.Edges = make([]DAGEdge, 0)
		}

		h.mu.Lock()
		h.dags[dag.DAGID] = &dag
		h.mu.Unlock()

		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(dag)

	case http.MethodGet:
		if subPath == "" {
			// GET /agentnet/dag — list all DAGs
			h.mu.RLock()
			list := make([]*DAG, 0, len(h.dags))
			for _, d := range h.dags {
				list = append(list, d)
			}
			h.mu.RUnlock()
			sort.Slice(list, func(i, j int) bool {
				return list[i].CreatedAt > list[j].CreatedAt
			})
			json.NewEncoder(w).Encode(list)
			return
		}

		parts := strings.SplitN(subPath, "/", 3)
		dagID := parts[0]

		h.mu.RLock()
		dag, ok := h.dags[dagID]
		h.mu.RUnlock()
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "dag not found"})
			return
		}

		if len(parts) == 2 && parts[1] == "ready" {
			// GET /agentnet/dag/{dag_id}/ready — get nodes whose deps are satisfied
			ready := h.getReadyNodes(dag)
			json.NewEncoder(w).Encode(map[string]any{
				"dag_id": dagID,
				"ready":  ready,
				"total":  len(dag.Nodes),
			})
			return
		}

		// GET /agentnet/dag/{dag_id} — get DAG status
		json.NewEncoder(w).Encode(dag)

	case http.MethodPut:
		// PUT /agentnet/dag/{dag_id}/node — add/update node
		// PUT /agentnet/dag/{dag_id}/edge — add/remove edge
		parts := strings.SplitN(subPath, "/", 3)
		if len(parts) < 2 {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "path must be {dag_id}/node or {dag_id}/edge"})
			return
		}
		dagID := parts[0]
		op := parts[1]

		h.mu.Lock()
		dag, ok := h.dags[dagID]
		if !ok {
			h.mu.Unlock()
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "dag not found"})
			return
		}

		switch op {
		case "node":
			var node DAGNode
			if err := json.NewDecoder(r.Body).Decode(&node); err != nil {
				h.mu.Unlock()
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
				return
			}
			// Remove node if action=remove
			if r.URL.Query().Get("action") == "remove" {
				for i, n := range dag.Nodes {
					if n.ID == node.ID {
						dag.Nodes = append(dag.Nodes[:i], dag.Nodes[i+1:]...)
						break
					}
				}
			} else {
				// Update existing or append new
				found := false
				for i, n := range dag.Nodes {
					if n.ID == node.ID {
						dag.Nodes[i] = node
						found = true
						break
					}
				}
				if !found {
					if node.Status == "" {
						node.Status = "pending"
					}
					dag.Nodes = append(dag.Nodes, node)
				}
			}
			dag.UpdatedAt = time.Now().UTC().Format(time.RFC3339)

		case "edge":
			var edge DAGEdge
			if err := json.NewDecoder(r.Body).Decode(&edge); err != nil {
				h.mu.Unlock()
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
				return
			}
			if r.URL.Query().Get("action") == "remove" {
				for i, e := range dag.Edges {
					if e.From == edge.From && e.To == edge.To {
						dag.Edges = append(dag.Edges[:i], dag.Edges[i+1:]...)
						break
					}
				}
			} else {
				dag.Edges = append(dag.Edges, edge)
			}
			dag.UpdatedAt = time.Now().UTC().Format(time.RFC3339)

		default:
			h.mu.Unlock()
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "operation must be 'node' or 'edge'"})
			return
		}

		h.mu.Unlock()
		json.NewEncoder(w).Encode(dag)

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// getReadyNodes returns nodes whose dependencies are all completed.
func (h *agentNetHandler) getReadyNodes(dag *DAG) []DAGNode {
	completed := make(map[string]bool)
	for _, n := range dag.Nodes {
		if n.Status == "completed" {
			completed[n.ID] = true
		}
	}

	var ready []DAGNode
	for _, n := range dag.Nodes {
		if n.Status != "pending" {
			continue
		}
		allDepsSatisfied := true
		for _, dep := range n.Dependencies {
			if !completed[dep] {
				allDepsSatisfied = false
				break
			}
		}
		if allDepsSatisfied {
			ready = append(ready, n)
		}
	}

	// Sort by priority (higher first)
	sort.Slice(ready, func(i, j int) bool {
		return ready[i].Priority > ready[j].Priority
	})

	return ready
}

// ── I4: Agent Spawn ────────────────────────────────────────────────────

func (h *agentNetHandler) serveSpawn(w http.ResponseWriter, r *http.Request, subPath string) {
	w.Header().Set("Content-Type", "application/json")

	switch r.Method {
	case http.MethodPost:
		// POST /agentnet/spawn — parent spawns child agent
		var spawn AgentSpawn
		if err := json.NewDecoder(r.Body).Decode(&spawn); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "invalid json: " + err.Error()})
			return
		}
		if spawn.ParentID == "" {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "parent_id is required"})
			return
		}
		spawn.SpawnID = "spawn-" + randomSuffix()
		if spawn.ChildID == "" {
			spawn.ChildID = "agent-" + randomSuffix()
		}
		spawn.Status = "created"
		spawn.CreatedAt = time.Now().UTC().Format(time.RFC3339)
		if spawn.TTLSeconds == 0 {
			spawn.TTLSeconds = 600 // default 10 min
		}

		h.mu.Lock()
		h.spawns[spawn.SpawnID] = &spawn

		// Auto-register child agent capability
		childCap := &AgentCapability{
			AgentID:       spawn.ChildID,
			DisplayName:   spawn.ChildName,
			Capabilities:  spawn.Capabilities,
			QualityScore:  0.8,
			CurrentLoad:   0,
			MaxConcurrent: 3,
			Status:        "idle",
			LastHeartbeat: spawn.CreatedAt,
			RegisteredAt:  spawn.CreatedAt,
		}
		h.capabilities[spawn.ChildID] = childCap
		h.mu.Unlock()

		// Publish spawn event
		h.publishAgentNetEvent(events.EventAgentSpawnRequested, "", "", map[string]any{
			"spawn_id":     spawn.SpawnID,
			"parent_id":    spawn.ParentID,
			"child_id":     spawn.ChildID,
			"child_name":   spawn.ChildName,
			"capabilities": spawn.Capabilities,
		})

		// Auto-transition to running
		go func() {
			time.Sleep(500 * time.Millisecond)
			h.mu.Lock()
			if s, ok := h.spawns[spawn.SpawnID]; ok {
				s.Status = "running"
			}
			h.mu.Unlock()

			h.publishAgentNetEvent(events.EventAgentSpawnCompleted, "", "", map[string]any{
				"spawn_id": spawn.SpawnID,
				"child_id": spawn.ChildID,
				"status":   "running",
			})

			// Auto-destroy after TTL
			time.Sleep(time.Duration(spawn.TTLSeconds) * time.Second)
			h.mu.Lock()
			if s, ok := h.spawns[spawn.SpawnID]; ok {
				s.Status = "destroyed"
				s.CompletedAt = time.Now().UTC().Format(time.RFC3339)
			}
			if c, ok := h.capabilities[spawn.ChildID]; ok {
				c.Status = "offline"
			}
			h.mu.Unlock()
			log.Printf("agentnet: spawn %s auto-destroyed after TTL", spawn.SpawnID)
		}()

		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(spawn)

	case http.MethodGet:
		if subPath == "" {
			// GET /agentnet/spawn — list all spawns
			h.mu.RLock()
			list := make([]*AgentSpawn, 0, len(h.spawns))
			for _, s := range h.spawns {
				list = append(list, s)
			}
			h.mu.RUnlock()
			sort.Slice(list, func(i, j int) bool {
				return list[i].CreatedAt > list[j].CreatedAt
			})
			json.NewEncoder(w).Encode(list)
			return
		}

		// GET /agentnet/spawn/{spawn_id} — get spawn status
		h.mu.RLock()
		spawn, ok := h.spawns[subPath]
		h.mu.RUnlock()
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "spawn not found"})
			return
		}
		json.NewEncoder(w).Encode(spawn)

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// ── I2: Shared Memory ──────────────────────────────────────────────────

func (h *agentNetHandler) serveMemory(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	switch r.Method {
	case http.MethodPost:
		// POST /agentnet/memory — write to shared memory
		var entry SharedMemoryEntry
		if err := json.NewDecoder(r.Body).Decode(&entry); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "invalid json: " + err.Error()})
			return
		}
		entry.ID = "mem-" + randomSuffix()
		entry.Timestamp = time.Now().UTC().Format(time.RFC3339)

		h.mu.Lock()
		h.memories = append(h.memories, entry)
		// Keep max 1000 entries in memory
		if len(h.memories) > 1000 {
			h.memories = h.memories[len(h.memories)-1000:]
		}
		h.mu.Unlock()

		h.publishAgentNetEvent(events.EventAgentMemoryShared, "", "", map[string]any{
			"memory_id": entry.ID,
			"agent_id":  entry.AgentID,
			"intent":    entry.Intent,
		})

		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(entry)

	case http.MethodGet:
		// GET /agentnet/memory — read shared memory
		agentFilter := r.URL.Query().Get("agent_id")
		intentFilter := r.URL.Query().Get("intent")
		limit := 50

		h.mu.RLock()
		defer h.mu.RUnlock()

		var results []SharedMemoryEntry
		// Iterate from newest
		for i := len(h.memories) - 1; i >= 0; i-- {
			m := h.memories[i]
			if agentFilter != "" && m.AgentID != agentFilter {
				continue
			}
			if intentFilter != "" && m.Intent != intentFilter {
				continue
			}
			results = append(results, m)
			if len(results) >= limit {
				break
			}
		}

		json.NewEncoder(w).Encode(results)

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// ── Heartbeat ──────────────────────────────────────────────────────────

func (h *agentNetHandler) serveHeartbeat(w http.ResponseWriter, r *http.Request, agentID string) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Content-Type", "application/json")

	h.mu.Lock()
	cap, ok := h.capabilities[agentID]
	if ok {
		cap.LastHeartbeat = time.Now().UTC().Format(time.RFC3339)
		cap.Status = "idle"

		// Decode optional load update
		var update struct {
			CurrentLoad int    `json:"current_load"`
			Status      string `json:"status"`
		}
		if r.Body != nil {
			json.NewDecoder(r.Body).Decode(&update)
		}
		if update.Status != "" {
			cap.Status = update.Status
		}
		if update.CurrentLoad > 0 {
			cap.CurrentLoad = update.CurrentLoad
		}
	}
	h.mu.Unlock()

	if !ok {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{"error": "agent not registered"})
		return
	}

	h.publishAgentNetEvent(events.EventAgentHeartbeat, "", "", map[string]any{
		"agent_id":     agentID,
		"status":       cap.Status,
		"current_load": cap.CurrentLoad,
	})

	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

// ── Stats & Topology ───────────────────────────────────────────────────

func (h *agentNetHandler) serveStats(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Content-Type", "application/json")

	h.mu.RLock()
	defer h.mu.RUnlock()

	stats := AgentNetStats{
		TotalAgents:   len(h.capabilities),
		AgentByStatus: make(map[string]int),
		TotalTasks:    len(h.tasks),
		TasksByStatus: make(map[string]int),
		MemoryEntries: len(h.memories),
	}

	var totalQuality float64
	for _, c := range h.capabilities {
		stats.AgentByStatus[c.Status]++
		if c.Status != "offline" {
			stats.ActiveAgents++
		}
		totalQuality += c.QualityScore
	}
	if stats.TotalAgents > 0 {
		stats.AvgQualityScore = totalQuality / float64(stats.TotalAgents)
	}

	for _, t := range h.tasks {
		stats.TasksByStatus[t.Status]++
	}

	for _, d := range h.dags {
		if d.Status == "running" {
			stats.ActiveDAGs++
		}
	}

	for _, s := range h.spawns {
		if s.Status == "created" || s.Status == "running" {
			stats.ActiveSpawns++
		}
	}

	json.NewEncoder(w).Encode(stats)
}

// TopologyResponse is the full graph data for the frontend visualization.
type TopologyResponse struct {
	Nodes     []TopologyNode `json:"nodes"`
	Edges     []TopologyEdge `json:"edges"`
	UpdatedAt string         `json:"updated_at"`
}

type TopologyNode struct {
	ID          string  `json:"id"`
	Label       string  `json:"label"`
	Type        string  `json:"type"` // agent, task, spawn
	Status      string  `json:"status"`
	Quality     float64 `json:"quality,omitempty"`
	Load        int     `json:"load,omitempty"`
	MaxLoad     int     `json:"max_load,omitempty"`
	Description string  `json:"description,omitempty"`
}

type TopologyEdge struct {
	From   string `json:"from"`
	To     string `json:"to"`
	Label  string `json:"label"`
	Status string `json:"status"` // active, completed, failed
}

func (h *agentNetHandler) serveTopology(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Content-Type", "application/json")

	h.mu.RLock()
	defer h.mu.RUnlock()

	var topo TopologyResponse

	// Agents as nodes
	for _, c := range h.capabilities {
		topo.Nodes = append(topo.Nodes, TopologyNode{
			ID:          c.AgentID,
			Label:       c.DisplayName,
			Type:        "agent",
			Status:      c.Status,
			Quality:     c.QualityScore,
			Load:        c.CurrentLoad,
			MaxLoad:     c.MaxConcurrent,
			Description: strings.Join(c.Capabilities, ", "),
		})
	}

	// Tasks as nodes
	for _, t := range h.tasks {
		topo.Nodes = append(topo.Nodes, TopologyNode{
			ID:          t.TaskID,
			Label:       t.Description,
			Type:        "task",
			Status:      t.Status,
			Description: t.RequiredCap,
		})
		// Task-spawn edges
		if t.ParentTaskID != "" {
			topo.Edges = append(topo.Edges, TopologyEdge{
				From:   t.ParentTaskID,
				To:     t.TaskID,
				Label:  "subtask",
				Status: t.Status,
			})
		}
	}

	// DAG edges
	for _, d := range h.dags {
		for _, e := range d.Edges {
			topo.Edges = append(topo.Edges, TopologyEdge{
				From:   e.From,
				To:     e.To,
				Label:  e.Label,
				Status: "active",
			})
		}
	}

	// Spawn edges
	for _, s := range h.spawns {
		topo.Edges = append(topo.Edges, TopologyEdge{
			From:   s.ParentID,
			To:     s.ChildID,
			Label:  "spawned",
			Status: s.Status,
		})
	}

	topo.UpdatedAt = time.Now().UTC().Format(time.RFC3339)

	json.NewEncoder(w).Encode(topo)
}

// ── Helpers ─────────────────────────────────────────────────────────────

func (h *agentNetHandler) publishAgentNetEvent(eventType events.EventType, tenantID, sessionID string, payload map[string]any) {
	if h.bus == nil {
		return
	}

	// Determine appropriate subject based on event type
	var subject string
	switch eventType {
	case events.EventAgentCapabilityAnnounced:
		subject = eventbus.AgentNetCapabilitiesSubject
	case events.EventAgentTaskPublished, events.EventAgentTaskCompleted:
		subject = eventbus.AgentNetTasksSubject
	case events.EventAgentSpawnRequested, events.EventAgentSpawnCompleted:
		subject = eventbus.AgentNetSpawnSubject
	case events.EventAgentMemoryShared:
		subject = eventbus.AgentNetMemorySubject
	default:
		subject = eventbus.AgentNetTasksSubject
	}

	env := events.NewEnvelope(
		eventType,
		tenantID,
		sessionID,
		"",
		events.Producer{Service: "gateway-service", Instance: h.instance},
		payload,
	)
	env.EventID = "evt-" + randomSuffix()

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := h.bus.PublishEnvelope(ctx, subject, env); err != nil {
		log.Printf("agentnet: publish %s failed: %v", eventType, err)
	}
}

func max(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}
