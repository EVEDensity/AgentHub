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

// ── J1: Agent Identity data types ────────────────────────────────────

type AgentIdentity struct {
	ID             string `json:"id"`
	AgentID        string `json:"agent_id"`
	TenantID       string `json:"tenant_id"`
	Email          string `json:"email"`
	SSHPubKey      string `json:"ssh_pubkey"`
	SSHKeyType     string `json:"ssh_key_type"`
	GPGKey         string `json:"gpg_key"`
	OAuth2Provider string `json:"oauth2_provider"`
	OAuth2Creds    string `json:"oauth2_creds"`
	Status         string `json:"status"` // pending, active, suspended, revoked
	CreatedAt      string `json:"created_at"`
	UpdatedAt      string `json:"updated_at"`
}

// ── J2: Sandbox data types ──────────────────────────────────────────

type SandboxContainer struct {
	ID             string   `json:"id"`
	AgentID        string   `json:"agent_id"`
	TenantID       string   `json:"tenant_id"`
	ContainerName  string   `json:"container_name"`
	Image          string   `json:"image"`
	Status         string   `json:"status"` // created, starting, running, stopped, failed, destroyed
	CPULimit       float64  `json:"cpu_limit"`
	MemoryMB       int      `json:"memory_mb"`
	DiskMB         int      `json:"disk_mb"`
	NetworkAllow   []string `json:"network_allow"`
	WorkspacePath  string   `json:"workspace_path"`
	SeccompProfile string   `json:"seccomp_profile"`
	StartedAt      string   `json:"started_at,omitempty"`
	StoppedAt      string   `json:"stopped_at,omitempty"`
	IdleTimeoutS   int      `json:"idle_timeout_s"`
	MaxRuntimeS    int      `json:"max_runtime_s"`
	CreatedAt      string   `json:"created_at"`
	UpdatedAt      string   `json:"updated_at"`
}

type SandboxExecLog struct {
	ID          string `json:"id"`
	ContainerID string `json:"container_id"`
	AgentID     string `json:"agent_id"`
	TenantID    string `json:"tenant_id"`
	Command     string `json:"command"`
	ExitCode    int    `json:"exit_code"`
	Stdout      string `json:"stdout"`
	Stderr      string `json:"stderr"`
	DurationMs  int    `json:"duration_ms"`
	ExecutedAt  string `json:"executed_at"`
}

type SandboxStats struct {
	TotalContainers   int            `json:"total_containers"`
	ActiveContainers  int            `json:"active_containers"`
	ByStatus          map[string]int `json:"by_status"`
	TotalExecs        int            `json:"total_execs"`
	AvgDurationMs     float64        `json:"avg_duration_ms"`
}

// ── Digital Identity Handler ──────────────────────────────────────────

// digitalIdentityHandler implements identity (J1) and sandbox (J2) REST APIs.
type digitalIdentityHandler struct {
	mu sync.RWMutex

	identities map[string]*AgentIdentity   // keyed by agent_id
	containers map[string]*SandboxContainer // keyed by container id
	execLogs   map[string][]SandboxExecLog  // keyed by container_id

	bus      *eventbus.Client
	instance string
}

func newDigitalIdentityHandler(bus *eventbus.Client) *digitalIdentityHandler {
	return &digitalIdentityHandler{
		identities: make(map[string]*AgentIdentity),
		containers: make(map[string]*SandboxContainer),
		execLogs:   make(map[string][]SandboxExecLog),
		bus:        bus,
		instance:   getenv("HOSTNAME", "local"),
	}
}

func (h *digitalIdentityHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}

	rel := strings.TrimPrefix(r.URL.Path, "/digital")
	rel = strings.TrimPrefix(rel, "/")

	// ── Identity routes ──────────────────────────────────────────
	if rel == "identity" || strings.HasPrefix(rel, "identity/") {
		h.serveIdentity(w, r, strings.TrimPrefix(rel, "identity/"))
		return
	}

	// ── Sandbox routes ──────────────────────────────────────────
	if rel == "sandbox" || strings.HasPrefix(rel, "sandbox/") {
		h.serveSandbox(w, r, strings.TrimPrefix(rel, "sandbox/"))
		return
	}

	// ── Sandbox stats ───────────────────────────────────────────
	if rel == "sandbox-stats" {
		h.serveSandboxStats(w, r)
		return
	}

	http.NotFound(w, r)
}

// ── J1: Identity CRUD ──────────────────────────────────────────────────

func (h *digitalIdentityHandler) serveIdentity(w http.ResponseWriter, r *http.Request, subPath string) {
	w.Header().Set("Content-Type", "application/json")

	// Parse sub-path: agent_id or agent_id/action
	parts := strings.SplitN(subPath, "/", 2)
	agentID := parts[0]
	action := ""
	if len(parts) > 1 {
		action = parts[1]
	}

	switch r.Method {
	case http.MethodPost:
		if agentID == "" {
			// POST /identity — create new identity
			var ident AgentIdentity
			if err := json.NewDecoder(r.Body).Decode(&ident); err != nil {
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "invalid json: " + err.Error()})
				return
			}
			if ident.AgentID == "" {
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "agent_id is required"})
				return
			}

			ident.ID = "ident-" + randomSuffix()
			if ident.Status == "" {
				ident.Status = "pending"
			}
			if ident.SSHKeyType == "" {
				ident.SSHKeyType = "ed25519"
			}
			now := time.Now().UTC().Format(time.RFC3339)
			ident.CreatedAt = now
			ident.UpdatedAt = now

			// Auto-generate email if not provided
			if ident.Email == "" {
				ident.Email = fmt.Sprintf("%s@agenthub.email", ident.AgentID)
			}

			// Auto-generate SSH key (MVP: placeholder — real keygen via crypto/ed25519 in prod)
			if ident.SSHPubKey == "" {
				ident.SSHPubKey = fmt.Sprintf("ssh-ed25519 AAAA...%s agenthub-agent", randomSuffix()[:8])
			}

			h.mu.Lock()
			h.identities[ident.AgentID] = &ident
			h.mu.Unlock()

			h.publishEvent(events.EventAgentIdentityCreated, ident.TenantID, "", map[string]any{
				"agent_id": ident.AgentID,
				"email":    ident.Email,
				"status":   ident.Status,
			})

			w.WriteHeader(http.StatusCreated)
			json.NewEncoder(w).Encode(ident)
			return
		}

		// POST /identity/{agent_id}/{action} — email, ssh, oauth2
		h.mu.Lock()
		ident, ok := h.identities[agentID]
		if !ok {
			h.mu.Unlock()
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "identity not found"})
			return
		}

		switch action {
		case "email":
			var req struct{ Email string }
			if err := json.NewDecoder(r.Body).Decode(&req); err == nil && req.Email != "" {
				ident.Email = req.Email
			} else {
				ident.Email = fmt.Sprintf("%s@agenthub.email", agentID)
			}

		case "ssh":
			ident.SSHKeyType = "ed25519"
			ident.SSHPubKey = fmt.Sprintf("ssh-ed25519 %s agenthub-agent-%s", randomSuffix()[:16], agentID)

		case "oauth2":
			var req struct {
				Provider string `json:"provider"`
				Creds    string `json:"creds"`
			}
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				h.mu.Unlock()
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
				return
			}
			ident.OAuth2Provider = req.Provider
			ident.OAuth2Creds = req.Creds

		default:
			h.mu.Unlock()
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "unknown action: " + action})
			return
		}

		ident.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
		h.mu.Unlock()

		h.publishEvent(events.EventAgentIdentityUpdated, ident.TenantID, "", map[string]any{
			"agent_id": agentID,
			"action":   action,
		})

		json.NewEncoder(w).Encode(ident)

	case http.MethodGet:
		if agentID == "" {
			// GET /identity — list all
			h.mu.RLock()
			list := make([]*AgentIdentity, 0, len(h.identities))
			for _, ident := range h.identities {
				list = append(list, ident)
			}
			h.mu.RUnlock()

			// Optional filters
			statusFilter := r.URL.Query().Get("status")
			if statusFilter != "" {
				filtered := make([]*AgentIdentity, 0)
				for _, ident := range list {
					if ident.Status == statusFilter {
						filtered = append(filtered, ident)
					}
				}
				list = filtered
			}

			sort.Slice(list, func(i, j int) bool {
				return list[i].CreatedAt > list[j].CreatedAt
			})

			json.NewEncoder(w).Encode(list)
			return
		}

		// GET /identity/{agent_id}
		h.mu.RLock()
		ident, ok := h.identities[agentID]
		h.mu.RUnlock()
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "identity not found"})
			return
		}
		json.NewEncoder(w).Encode(ident)

	case http.MethodPut:
		// PUT /identity/{agent_id} — update identity
		h.mu.Lock()
		ident, ok := h.identities[agentID]
		if !ok {
			h.mu.Unlock()
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "identity not found"})
			return
		}

		var update AgentIdentity
		if err := json.NewDecoder(r.Body).Decode(&update); err != nil {
			h.mu.Unlock()
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
			return
		}

		if update.Status != "" {
			ident.Status = update.Status
		}
		if update.Email != "" {
			ident.Email = update.Email
		}
		ident.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
		h.mu.Unlock()

		h.publishEvent(events.EventAgentIdentityUpdated, ident.TenantID, "", map[string]any{
			"agent_id": agentID,
			"status":   ident.Status,
		})

		json.NewEncoder(w).Encode(ident)

	case http.MethodDelete:
		// DELETE /identity/{agent_id}
		h.mu.Lock()
		delete(h.identities, agentID)
		h.mu.Unlock()
		w.WriteHeader(http.StatusNoContent)

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// ── J2: Sandbox Container Lifecycle ────────────────────────────────────

func (h *digitalIdentityHandler) serveSandbox(w http.ResponseWriter, r *http.Request, subPath string) {
	w.Header().Set("Content-Type", "application/json")

	// Parse: container_id or container_id/action
	parts := strings.SplitN(subPath, "/", 2)
	containerID := parts[0]
	action := ""
	if len(parts) > 1 {
		action = parts[1]
	}

	switch r.Method {
	case http.MethodPost:
		if containerID == "" {
			// POST /sandbox/containers — create container
			var c SandboxContainer
			if err := json.NewDecoder(r.Body).Decode(&c); err != nil {
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "invalid json: " + err.Error()})
				return
			}
			if c.AgentID == "" {
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "agent_id is required"})
				return
			}

			c.ID = "sandbox-" + randomSuffix()
			if c.Image == "" {
				c.Image = "agenthub/sandbox:latest"
			}
			if c.Status == "" {
				c.Status = "created"
			}
			if c.CPULimit == 0 {
				c.CPULimit = 1.0
			}
			if c.MemoryMB == 0 {
				c.MemoryMB = 512
			}
			if c.DiskMB == 0 {
				c.DiskMB = 10240
			}
			if c.IdleTimeoutS == 0 {
				c.IdleTimeoutS = 1800
			}
			if c.MaxRuntimeS == 0 {
				c.MaxRuntimeS = 7200
			}
			if c.SeccompProfile == "" {
				c.SeccompProfile = "default"
			}
			if c.ContainerName == "" {
				c.ContainerName = fmt.Sprintf("ah-sandbox-%s", c.AgentID)
			}
			if c.WorkspacePath == "" {
				c.WorkspacePath = fmt.Sprintf("/workspace/%s", c.AgentID)
			}
			now := time.Now().UTC().Format(time.RFC3339)
			c.CreatedAt = now
			c.UpdatedAt = now

			h.mu.Lock()
			h.containers[c.ID] = &c
			h.execLogs[c.ID] = make([]SandboxExecLog, 0)
			h.mu.Unlock()

			w.WriteHeader(http.StatusCreated)
			json.NewEncoder(w).Encode(c)
			return
		}

		// POST /sandbox/containers/{id}/{action} — start, stop, exec
		h.mu.Lock()
		c, ok := h.containers[containerID]
		if !ok {
			h.mu.Unlock()
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "container not found"})
			return
		}

		switch action {
		case "start":
			c.Status = "starting"
			c.StartedAt = time.Now().UTC().Format(time.RFC3339)
			h.mu.Unlock()

			// Simulate container startup (MVP: would call Docker SDK)
			go func(containerID string) {
				time.Sleep(300 * time.Millisecond)
				h.mu.Lock()
				if cc, ok2 := h.containers[containerID]; ok2 {
					cc.Status = "running"
					cc.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
				}
				h.mu.Unlock()

				h.publishEvent(events.EventSandboxContainerStarted, "", "", map[string]any{
					"container_id": containerID,
					"status":       "running",
				})

				// Auto-stop after max runtime
				time.Sleep(time.Duration(c.MaxRuntimeS) * time.Second)
				h.mu.Lock()
				if cc, ok2 := h.containers[containerID]; ok2 && cc.Status == "running" {
					cc.Status = "stopped"
					cc.StoppedAt = time.Now().UTC().Format(time.RFC3339)
					cc.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
				}
				h.mu.Unlock()
			}(containerID)

			json.NewEncoder(w).Encode(c)
			return

		case "stop":
			c.Status = "stopped"
			now := time.Now().UTC().Format(time.RFC3339)
			c.StoppedAt = now
			c.UpdatedAt = now
			h.mu.Unlock()

			h.publishEvent(events.EventSandboxContainerStopped, "", "", map[string]any{
				"container_id": containerID,
				"status":       "stopped",
			})

			json.NewEncoder(w).Encode(c)
			return

		case "exec":
			// Execute a command inside the container
			var req struct {
				Command string `json:"command"`
			}
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Command == "" {
				h.mu.Unlock()
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "command is required"})
				return
			}

			// Simulate command execution (MVP: would call Docker SDK exec)
			execLog := SandboxExecLog{
				ID:          "exec-" + randomSuffix(),
				ContainerID: containerID,
				AgentID:     c.AgentID,
				TenantID:    c.TenantID,
				Command:     req.Command,
				ExitCode:    0,
				DurationMs:  int(time.Now().UnixMilli()%100) + 50,
				ExecutedAt:  time.Now().UTC().Format(time.RFC3339),
			}

			// Simulate output for common commands
			switch {
			case strings.Contains(req.Command, "pip install"):
				execLog.Stdout = fmt.Sprintf("Successfully installed %s", strings.TrimPrefix(req.Command, "pip install "))
				execLog.DurationMs = 1500
			case strings.Contains(req.Command, "python"):
				execLog.Stdout = "Python 3.11.9\n>>> Hello from sandbox!"
			case strings.Contains(req.Command, "ls"):
				execLog.Stdout = "main.py\nrequirements.txt\noutput/"
			case strings.Contains(req.Command, "echo"):
				execLog.Stdout = strings.TrimPrefix(req.Command, "echo ")
			default:
				execLog.Stdout = fmt.Sprintf("[sandbox] executed: %s", req.Command)
			}

			h.execLogs[containerID] = append(h.execLogs[containerID], execLog)
			// Keep max 200 logs
			if len(h.execLogs[containerID]) > 200 {
				h.execLogs[containerID] = h.execLogs[containerID][len(h.execLogs[containerID])-200:]
			}
			h.mu.Unlock()

			h.publishEvent(events.EventSandboxExecCompleted, "", "", map[string]any{
				"container_id": containerID,
				"command":      req.Command,
				"exit_code":    execLog.ExitCode,
			})

			json.NewEncoder(w).Encode(execLog)
			return

		default:
			h.mu.Unlock()
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "unknown action: " + action})
			return
		}

	case http.MethodGet:
		if containerID == "" {
			// GET /sandbox/containers — list all
			h.mu.RLock()
			list := make([]*SandboxContainer, 0, len(h.containers))
			for _, c := range h.containers {
				list = append(list, c)
			}
			h.mu.RUnlock()

			statusFilter := r.URL.Query().Get("status")
			agentFilter := r.URL.Query().Get("agent_id")
			if statusFilter != "" || agentFilter != "" {
				filtered := make([]*SandboxContainer, 0)
				for _, c := range list {
					if statusFilter != "" && c.Status != statusFilter {
						continue
					}
					if agentFilter != "" && c.AgentID != agentFilter {
						continue
					}
					filtered = append(filtered, c)
				}
				list = filtered
			}

			sort.Slice(list, func(i, j int) bool {
				return list[i].CreatedAt > list[j].CreatedAt
			})
			json.NewEncoder(w).Encode(list)
			return
		}

		if action == "logs" {
			// GET /sandbox/containers/{id}/logs — get execution logs
			h.mu.RLock()
			logs, _ := h.execLogs[containerID]
			h.mu.RUnlock()
			json.NewEncoder(w).Encode(logs)
			return
		}

		// GET /sandbox/containers/{id} — get container
		h.mu.RLock()
		c, ok := h.containers[containerID]
		h.mu.RUnlock()
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "container not found"})
			return
		}
		json.NewEncoder(w).Encode(c)

	case http.MethodDelete:
		// DELETE /sandbox/containers/{id} — stop & remove
		h.mu.Lock()
		c, ok := h.containers[containerID]
		if ok {
			c.Status = "destroyed"
			c.StoppedAt = time.Now().UTC().Format(time.RFC3339)
			c.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
		}
		delete(h.containers, containerID)
		delete(h.execLogs, containerID)
		h.mu.Unlock()

		if !ok {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "container not found"})
			return
		}
		w.WriteHeader(http.StatusNoContent)

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// ── Sandbox Stats ──────────────────────────────────────────────────────

func (h *digitalIdentityHandler) serveSandboxStats(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Content-Type", "application/json")

	h.mu.RLock()
	defer h.mu.RUnlock()

	stats := SandboxStats{
		TotalContainers: len(h.containers),
		ByStatus:        make(map[string]int),
	}

	var totalDuration int
	for _, c := range h.containers {
		stats.ByStatus[c.Status]++
		if c.Status == "running" {
			stats.ActiveContainers++
		}
	}

	for _, logs := range h.execLogs {
		stats.TotalExecs += len(logs)
		for _, l := range logs {
			totalDuration += l.DurationMs
		}
	}

	if stats.TotalExecs > 0 {
		stats.AvgDurationMs = float64(totalDuration) / float64(stats.TotalExecs)
	}

	json.NewEncoder(w).Encode(stats)
}

// ── NATS publish helper ────────────────────────────────────────────────

func (h *digitalIdentityHandler) publishEvent(eventType events.EventType, tenantID, sessionID string, payload map[string]any) {
	if h.bus == nil {
		return
	}

	var subject string
	switch eventType {
	case events.EventAgentIdentityCreated, events.EventAgentIdentityUpdated:
		subject = eventbus.AgentIdentitySubject
	case events.EventSandboxContainerStarted, events.EventSandboxContainerStopped:
		subject = eventbus.SandboxControlSubject
	case events.EventSandboxExecCompleted:
		subject = eventbus.SandboxExecSubject
	default:
		subject = eventbus.AgentIdentitySubject
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
		log.Printf("digital-identity: publish %s failed: %v", eventType, err)
	}
}
