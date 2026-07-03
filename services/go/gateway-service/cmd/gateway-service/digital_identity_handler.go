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

	"github.com/agenthub/platform/shared/db"
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

// ── J5: Sandbox data types ──────────────────────────────────────────

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
	TotalContainers  int            `json:"total_containers"`
	ActiveContainers int            `json:"active_containers"`
	ByStatus         map[string]int `json:"by_status"`
	TotalExecs       int            `json:"total_execs"`
	AvgDurationMs    float64        `json:"avg_duration_ms"`
}

// ── Digital Identity Handler ──────────────────────────────────────────

// digitalIdentityHandler implements identity (J1) and sandbox (J5) REST APIs.
// PostgreSQL persistence is used when a db.Pool is available; in-memory maps
// serve as the fallback for development without a database.
type digitalIdentityHandler struct {
	mu sync.RWMutex

	identities map[string]*AgentIdentity    // keyed by agent_id
	containers map[string]*SandboxContainer // keyed by container id
	execLogs   map[string][]SandboxExecLog  // keyed by container_id

	pool     *db.Pool
	bus      *eventbus.Client
	instance string
}

func newDigitalIdentityHandler(bus *eventbus.Client, pool *db.Pool) *digitalIdentityHandler {
	return &digitalIdentityHandler{
		identities: make(map[string]*AgentIdentity),
		containers: make(map[string]*SandboxContainer),
		execLogs:   make(map[string][]SandboxExecLog),
		pool:       pool,
		bus:        bus,
		instance:   getenv("HOSTNAME", "local"),
	}
}

// pg reports whether PostgreSQL persistence is available.
func (h *digitalIdentityHandler) pg() bool { return h.pool != nil }

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

	if rel == "identity" || strings.HasPrefix(rel, "identity/") {
		h.serveIdentity(w, r, strings.TrimPrefix(rel, "identity/"))
		return
	}

	if rel == "sandbox" || strings.HasPrefix(rel, "sandbox/") {
		h.serveSandbox(w, r, strings.TrimPrefix(rel, "sandbox/"))
		return
	}

	if rel == "sandbox-stats" {
		h.serveSandboxStats(w, r)
		return
	}

	http.NotFound(w, r)
}

// ── J1: Identity CRUD ──────────────────────────────────────────────────

func (h *digitalIdentityHandler) serveIdentity(w http.ResponseWriter, r *http.Request, subPath string) {
	w.Header().Set("Content-Type", "application/json")

	parts := strings.SplitN(subPath, "/", 2)
	agentID := parts[0]
	action := ""
	if len(parts) > 1 {
		action = parts[1]
	}

	switch r.Method {
	case http.MethodPost:
		if agentID == "" {
			h.createIdentity(w, r)
			return
		}
		h.identityAction(w, r, agentID, action)

	case http.MethodGet:
		h.getIdentity(w, r, agentID)

	case http.MethodPut:
		h.updateIdentity(w, r, agentID)

	case http.MethodDelete:
		h.deleteIdentity(w, r, agentID)

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// ── Identity: PG helpers ───────────────────────────────────────────────

func (h *digitalIdentityHandler) createIdentityPG(r *http.Request, ident AgentIdentity) error {
	_, err := h.pool.Exec(r.Context(),
		`INSERT INTO agentnet_identities (id, agent_id, tenant_id, email, ssh_pubkey, ssh_key_type, gpg_key, oauth2_provider, oauth2_creds, status, created_at, updated_at)
		 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
		 ON CONFLICT (agent_id) DO UPDATE SET
		 email=EXCLUDED.email, ssh_pubkey=EXCLUDED.ssh_pubkey, ssh_key_type=EXCLUDED.ssh_key_type,
		 gpg_key=EXCLUDED.gpg_key, oauth2_provider=EXCLUDED.oauth2_provider, oauth2_creds=EXCLUDED.oauth2_creds,
		 status=EXCLUDED.status, updated_at=EXCLUDED.updated_at`,
		ident.ID, ident.AgentID, ident.TenantID, ident.Email, ident.SSHPubKey, ident.SSHKeyType,
		ident.GPGKey, ident.OAuth2Provider, ident.OAuth2Creds, ident.Status, ident.CreatedAt, ident.UpdatedAt)
	return err
}

func (h *digitalIdentityHandler) getIdentityPG(r *http.Request, agentID string) (*AgentIdentity, error) {
	var ident AgentIdentity
	var sshPub, gpg, oa2p, oa2c string
	err := h.pool.QueryRow(r.Context(),
		`SELECT id, agent_id, tenant_id, email, ssh_pubkey, ssh_key_type, gpg_key, oauth2_provider, oauth2_creds, status, created_at, updated_at
		 FROM agentnet_identities WHERE agent_id=$1`, agentID).
		Scan(&ident.ID, &ident.AgentID, &ident.TenantID, &ident.Email, &sshPub, &ident.SSHKeyType,
			&gpg, &oa2p, &oa2c, &ident.Status, &ident.CreatedAt, &ident.UpdatedAt)
	if err != nil {
		return nil, err
	}
	ident.SSHPubKey = sshPub
	ident.GPGKey = gpg
	ident.OAuth2Provider = oa2p
	ident.OAuth2Creds = oa2c
	return &ident, nil
}

func (h *digitalIdentityHandler) listIdentitiesPG(r *http.Request, statusFilter string) ([]*AgentIdentity, error) {
	query := `SELECT id, agent_id, tenant_id, email, ssh_pubkey, ssh_key_type, gpg_key, oauth2_provider, oauth2_creds, status, created_at, updated_at FROM agentnet_identities WHERE 1=1`
	args := []any{}
	argIdx := 1
	if statusFilter != "" {
		query += " AND status=$" + itoa(argIdx)
		args = append(args, statusFilter)
		argIdx++
	}
	query += " ORDER BY created_at DESC"

	rows, err := h.pool.Query(r.Context(), query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var list []*AgentIdentity
	for rows.Next() {
		var ident AgentIdentity
		var sshPub, gpg, oa2p, oa2c string
		if err := rows.Scan(&ident.ID, &ident.AgentID, &ident.TenantID, &ident.Email,
			&sshPub, &ident.SSHKeyType, &gpg, &oa2p, &oa2c,
			&ident.Status, &ident.CreatedAt, &ident.UpdatedAt); err == nil {
			ident.SSHPubKey = sshPub
			ident.GPGKey = gpg
			ident.OAuth2Provider = oa2p
			ident.OAuth2Creds = oa2c
			list = append(list, &ident)
		}
	}
	return list, nil
}

func (h *digitalIdentityHandler) updateIdentityPG(r *http.Request, agentID string, ident *AgentIdentity) error {
	_, err := h.pool.Exec(r.Context(),
		`UPDATE agentnet_identities SET status=$1, email=$2, updated_at=$3 WHERE agent_id=$4`,
		ident.Status, ident.Email, ident.UpdatedAt, agentID)
	return err
}

func (h *digitalIdentityHandler) deleteIdentityPG(r *http.Request, agentID string) error {
	_, err := h.pool.Exec(r.Context(), `DELETE FROM agentnet_identities WHERE agent_id=$1`, agentID)
	return err
}

func (h *digitalIdentityHandler) identityEmailPG(r *http.Request, agentID, email string) error {
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := h.pool.Exec(r.Context(), `UPDATE agentnet_identities SET email=$1, updated_at=$2 WHERE agent_id=$3`, email, now, agentID)
	return err
}

func (h *digitalIdentityHandler) identitySSHPG(r *http.Request, agentID, sshPubKey, sshKeyType string) error {
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := h.pool.Exec(r.Context(), `UPDATE agentnet_identities SET ssh_pubkey=$1, ssh_key_type=$2, updated_at=$3 WHERE agent_id=$4`, sshPubKey, sshKeyType, now, agentID)
	return err
}

func (h *digitalIdentityHandler) identityOAuth2PG(r *http.Request, agentID, provider, creds string) error {
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := h.pool.Exec(r.Context(), `UPDATE agentnet_identities SET oauth2_provider=$1, oauth2_creds=$2, updated_at=$3 WHERE agent_id=$4`, provider, creds, now, agentID)
	return err
}

// ── Identity: handlers ─────────────────────────────────────────────────

func (h *digitalIdentityHandler) createIdentity(w http.ResponseWriter, r *http.Request) {
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

	if ident.Email == "" {
		ident.Email = fmt.Sprintf("%s@agenthub.email", ident.AgentID)
	}
	if ident.SSHPubKey == "" {
		ident.SSHPubKey = fmt.Sprintf("ssh-ed25519 AAAA...%s agenthub-agent", randomSuffix()[:8])
	}

	// PG path
	if h.pg() {
		if err := h.createIdentityPG(r, ident); err != nil {
			log.Printf("digital-identity: pg create identity failed: %v", err)
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "pg create failed: " + err.Error()})
			return
		}
		h.publishEvent(events.EventAgentIdentityCreated, ident.TenantID, "", map[string]any{
			"agent_id": ident.AgentID, "email": ident.Email, "status": ident.Status,
		})
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(ident)
		return
	}

	// Memory fallback
	h.mu.Lock()
	h.identities[ident.AgentID] = &ident
	h.mu.Unlock()

	h.publishEvent(events.EventAgentIdentityCreated, ident.TenantID, "", map[string]any{
		"agent_id": ident.AgentID, "email": ident.Email, "status": ident.Status,
	})

	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(ident)
}

func (h *digitalIdentityHandler) getIdentity(w http.ResponseWriter, r *http.Request, agentID string) {
	// PG path
	if h.pg() {
		if agentID == "" {
			statusFilter := r.URL.Query().Get("status")
			list, err := h.listIdentitiesPG(r, statusFilter)
			if err != nil {
				w.WriteHeader(http.StatusInternalServerError)
				json.NewEncoder(w).Encode(map[string]string{"error": "pg list failed: " + err.Error()})
				return
			}
			json.NewEncoder(w).Encode(list)
			return
		}
		ident, err := h.getIdentityPG(r, agentID)
		if err != nil {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "identity not found"})
			return
		}
		json.NewEncoder(w).Encode(ident)
		return
	}

	// Memory fallback
	if agentID == "" {
		h.mu.RLock()
		list := make([]*AgentIdentity, 0, len(h.identities))
		for _, ident := range h.identities {
			list = append(list, ident)
		}
		h.mu.RUnlock()

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

		sort.Slice(list, func(i, j int) bool { return list[i].CreatedAt > list[j].CreatedAt })
		json.NewEncoder(w).Encode(list)
		return
	}

	h.mu.RLock()
	ident, ok := h.identities[agentID]
	h.mu.RUnlock()
	if !ok {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{"error": "identity not found"})
		return
	}
	json.NewEncoder(w).Encode(ident)
}

func (h *digitalIdentityHandler) updateIdentity(w http.ResponseWriter, r *http.Request, agentID string) {
	// PG path
	if h.pg() {
		var update AgentIdentity
		if err := json.NewDecoder(r.Body).Decode(&update); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
			return
		}
		existing, err := h.getIdentityPG(r, agentID)
		if err != nil {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "identity not found"})
			return
		}
		if update.Status != "" {
			existing.Status = update.Status
		}
		if update.Email != "" {
			existing.Email = update.Email
		}
		existing.UpdatedAt = time.Now().UTC().Format(time.RFC3339)

		if err := h.updateIdentityPG(r, agentID, existing); err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "pg update failed: " + err.Error()})
			return
		}

		h.publishEvent(events.EventAgentIdentityUpdated, existing.TenantID, "", map[string]any{
			"agent_id": agentID, "status": existing.Status,
		})
		json.NewEncoder(w).Encode(existing)
		return
	}

	// Memory fallback
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
		"agent_id": agentID, "status": ident.Status,
	})

	json.NewEncoder(w).Encode(ident)
}

func (h *digitalIdentityHandler) deleteIdentity(w http.ResponseWriter, r *http.Request, agentID string) {
	// PG path
	if h.pg() {
		if err := h.deleteIdentityPG(r, agentID); err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "pg delete failed: " + err.Error()})
			return
		}
		w.WriteHeader(http.StatusNoContent)
		return
	}

	// Memory fallback
	h.mu.Lock()
	delete(h.identities, agentID)
	h.mu.Unlock()
	w.WriteHeader(http.StatusNoContent)
}

func (h *digitalIdentityHandler) identityAction(w http.ResponseWriter, r *http.Request, agentID, action string) {
	// PG path
	if h.pg() {
		ident, err := h.getIdentityPG(r, agentID)
		if err != nil {
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
			ident.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
			if err := h.identityEmailPG(r, agentID, ident.Email); err != nil {
				w.WriteHeader(http.StatusInternalServerError)
				json.NewEncoder(w).Encode(map[string]string{"error": "pg email update failed"})
				return
			}

		case "ssh":
			ident.SSHKeyType = "ed25519"
			ident.SSHPubKey = fmt.Sprintf("ssh-ed25519 %s agenthub-agent-%s", randomSuffix()[:16], agentID)
			ident.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
			if err := h.identitySSHPG(r, agentID, ident.SSHPubKey, ident.SSHKeyType); err != nil {
				w.WriteHeader(http.StatusInternalServerError)
				json.NewEncoder(w).Encode(map[string]string{"error": "pg ssh update failed"})
				return
			}

		case "oauth2":
			var req struct {
				Provider string `json:"provider"`
				Creds    string `json:"creds"`
			}
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
				return
			}
			ident.OAuth2Provider = req.Provider
			ident.OAuth2Creds = req.Creds
			ident.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
			if err := h.identityOAuth2PG(r, agentID, req.Provider, req.Creds); err != nil {
				w.WriteHeader(http.StatusInternalServerError)
				json.NewEncoder(w).Encode(map[string]string{"error": "pg oauth2 update failed"})
				return
			}

		default:
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "unknown action: " + action})
			return
		}

		h.publishEvent(events.EventAgentIdentityUpdated, ident.TenantID, "", map[string]any{
			"agent_id": agentID, "action": action,
		})
		json.NewEncoder(w).Encode(ident)
		return
	}

	// Memory fallback
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
		"agent_id": agentID, "action": action,
	})

	json.NewEncoder(w).Encode(ident)
}

// ── J5: Sandbox Container Lifecycle ────────────────────────────────────

// ── Sandbox: PG helpers ────────────────────────────────────────────────

func (h *digitalIdentityHandler) createContainerPG(r *http.Request, c *SandboxContainer) error {
	_, err := h.pool.Exec(r.Context(),
		`INSERT INTO sandbox_containers (id, agent_id, tenant_id, container_name, image, status, cpu_limit, memory_mb, disk_mb, network_allow, workspace_path, seccomp_profile, idle_timeout_s, max_runtime_s, created_at, updated_at)
		 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)`,
		c.ID, c.AgentID, c.TenantID, c.ContainerName, c.Image, c.Status,
		c.CPULimit, c.MemoryMB, c.DiskMB, c.NetworkAllow, c.WorkspacePath,
		c.SeccompProfile, c.IdleTimeoutS, c.MaxRuntimeS, c.CreatedAt, c.UpdatedAt)
	return err
}

func (h *digitalIdentityHandler) getContainerPG(r *http.Request, containerID string) (*SandboxContainer, error) {
	var c SandboxContainer
	var startedAt, stoppedAt *time.Time
	err := h.pool.QueryRow(r.Context(),
		`SELECT id, agent_id, tenant_id, container_name, image, status, cpu_limit, memory_mb, disk_mb, network_allow, workspace_path, seccomp_profile, idle_timeout_s, max_runtime_s, started_at, stopped_at, created_at, updated_at
		 FROM sandbox_containers WHERE id=$1`, containerID).
		Scan(&c.ID, &c.AgentID, &c.TenantID, &c.ContainerName, &c.Image, &c.Status,
			&c.CPULimit, &c.MemoryMB, &c.DiskMB, &c.NetworkAllow,
			&c.WorkspacePath, &c.SeccompProfile, &c.IdleTimeoutS, &c.MaxRuntimeS,
			&startedAt, &stoppedAt, &c.CreatedAt, &c.UpdatedAt)
	if err != nil {
		return nil, err
	}
	if startedAt != nil {
		c.StartedAt = startedAt.Format(time.RFC3339)
	}
	if stoppedAt != nil {
		c.StoppedAt = stoppedAt.Format(time.RFC3339)
	}
	return &c, nil
}

func (h *digitalIdentityHandler) listContainersPG(r *http.Request, statusFilter, agentFilter string) ([]*SandboxContainer, error) {
	query := `SELECT id, agent_id, tenant_id, container_name, image, status, cpu_limit, memory_mb, disk_mb, network_allow, workspace_path, seccomp_profile, idle_timeout_s, max_runtime_s, started_at, stopped_at, created_at, updated_at FROM sandbox_containers WHERE 1=1`
	args := []any{}
	argIdx := 1
	if statusFilter != "" {
		query += " AND status=$" + itoa(argIdx)
		args = append(args, statusFilter)
		argIdx++
	}
	if agentFilter != "" {
		query += " AND agent_id=$" + itoa(argIdx)
		args = append(args, agentFilter)
		argIdx++
	}
	query += " ORDER BY created_at DESC"

	rows, err := h.pool.Query(r.Context(), query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var list []*SandboxContainer
	for rows.Next() {
		var c SandboxContainer
		var startedAt, stoppedAt *time.Time
		if err := rows.Scan(&c.ID, &c.AgentID, &c.TenantID, &c.ContainerName, &c.Image,
			&c.Status, &c.CPULimit, &c.MemoryMB, &c.DiskMB, &c.NetworkAllow,
			&c.WorkspacePath, &c.SeccompProfile, &c.IdleTimeoutS, &c.MaxRuntimeS,
			&startedAt, &stoppedAt, &c.CreatedAt, &c.UpdatedAt); err == nil {
			if startedAt != nil {
				c.StartedAt = startedAt.Format(time.RFC3339)
			}
			if stoppedAt != nil {
				c.StoppedAt = stoppedAt.Format(time.RFC3339)
			}
			list = append(list, &c)
		}
	}
	return list, nil
}

func (h *digitalIdentityHandler) updateContainerStatusPG(r *http.Request, containerID, status string) error {
	now := time.Now().UTC()
	startedAt := &now
	if status != "starting" && status != "running" {
		startedAt = nil
	}
	stoppedAt := &now
	if status != "stopped" && status != "failed" && status != "destroyed" {
		stoppedAt = nil
	}
	_, err := h.pool.Exec(r.Context(),
		`UPDATE sandbox_containers SET status=$1, started_at=$2, stopped_at=$3, updated_at=$4 WHERE id=$5`,
		status, startedAt, stoppedAt, now, containerID)
	return err
}

func (h *digitalIdentityHandler) deleteContainerPG(r *http.Request, containerID string) error {
	_, err := h.pool.Exec(r.Context(), `DELETE FROM sandbox_containers WHERE id=$1`, containerID)
	return err
}

func (h *digitalIdentityHandler) insertExecLogPG(r *http.Request, log SandboxExecLog) error {
	_, err := h.pool.Exec(r.Context(),
		`INSERT INTO sandbox_exec_logs (id, container_id, agent_id, tenant_id, command, exit_code, stdout, stderr, duration_ms, executed_at)
		 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
		log.ID, log.ContainerID, log.AgentID, log.TenantID, log.Command,
		log.ExitCode, log.Stdout, log.Stderr, log.DurationMs, log.ExecutedAt)
	return err
}

func (h *digitalIdentityHandler) getExecLogsPG(r *http.Request, containerID string) ([]SandboxExecLog, error) {
	rows, err := h.pool.Query(r.Context(),
		`SELECT id, container_id, agent_id, tenant_id, command, exit_code, stdout, stderr, duration_ms, executed_at
		 FROM sandbox_exec_logs WHERE container_id=$1 ORDER BY executed_at ASC`, containerID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var logs []SandboxExecLog
	for rows.Next() {
		var l SandboxExecLog
		if err := rows.Scan(&l.ID, &l.ContainerID, &l.AgentID, &l.TenantID, &l.Command,
			&l.ExitCode, &l.Stdout, &l.Stderr, &l.DurationMs, &l.ExecutedAt); err == nil {
			logs = append(logs, l)
		}
	}
	return logs, nil
}

func (h *digitalIdentityHandler) computeSandboxStatsPG(r *http.Request) (SandboxStats, error) {
	stats := SandboxStats{ByStatus: make(map[string]int)}

	// Container totals by status
	rows, err := h.pool.Query(r.Context(),
		`SELECT status, COUNT(*) FROM sandbox_containers GROUP BY status`)
	if err != nil {
		return stats, err
	}
	defer rows.Close()

	for rows.Next() {
		var status string
		var cnt int
		if err := rows.Scan(&status, &cnt); err == nil {
			stats.ByStatus[status] = cnt
			stats.TotalContainers += cnt
			if status == "running" || status == "starting" {
				stats.ActiveContainers += cnt
			}
		}
	}

	// Exec stats
	_ = h.pool.QueryRow(r.Context(),
		`SELECT COUNT(*), COALESCE(AVG(duration_ms), 0) FROM sandbox_exec_logs`).
		Scan(&stats.TotalExecs, &stats.AvgDurationMs)

	return stats, nil
}

// ── Sandbox: handlers ──────────────────────────────────────────────────

func (h *digitalIdentityHandler) serveSandbox(w http.ResponseWriter, r *http.Request, subPath string) {
	w.Header().Set("Content-Type", "application/json")

	parts := strings.SplitN(subPath, "/", 2)
	containerID := parts[0]
	action := ""
	if len(parts) > 1 {
		action = parts[1]
	}

	switch r.Method {
	case http.MethodPost:
		if containerID == "" {
			h.createContainer(w, r)
			return
		}
		h.containerAction(w, r, containerID, action)

	case http.MethodGet:
		if containerID == "" {
			h.listContainers(w, r)
			return
		}
		if action == "logs" {
			h.getExecLogs(w, r, containerID)
			return
		}
		h.getContainer(w, r, containerID)

	case http.MethodDelete:
		h.deleteContainer(w, r, containerID)

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

func (h *digitalIdentityHandler) createContainer(w http.ResponseWriter, r *http.Request) {
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

	// PG path
	if h.pg() {
		if err := h.createContainerPG(r, &c); err != nil {
			log.Printf("digital-identity: pg create container failed: %v", err)
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "pg create failed: " + err.Error()})
			return
		}
		sandboxLifecycle.WithLabelValues("created").Inc()
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(c)
		return
	}

	// Memory fallback
	h.mu.Lock()
	h.containers[c.ID] = &c
	h.execLogs[c.ID] = make([]SandboxExecLog, 0)
	h.mu.Unlock()

	sandboxLifecycle.WithLabelValues("created").Inc()

	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(c)
}

func (h *digitalIdentityHandler) getContainer(w http.ResponseWriter, r *http.Request, containerID string) {
	// PG path
	if h.pg() {
		c, err := h.getContainerPG(r, containerID)
		if err != nil {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "container not found"})
			return
		}
		json.NewEncoder(w).Encode(c)
		return
	}

	// Memory fallback
	h.mu.RLock()
	c, ok := h.containers[containerID]
	h.mu.RUnlock()
	if !ok {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{"error": "container not found"})
		return
	}
	json.NewEncoder(w).Encode(c)
}

func (h *digitalIdentityHandler) listContainers(w http.ResponseWriter, r *http.Request) {
	// PG path
	if h.pg() {
		statusFilter := r.URL.Query().Get("status")
		agentFilter := r.URL.Query().Get("agent_id")
		list, err := h.listContainersPG(r, statusFilter, agentFilter)
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "pg list failed: " + err.Error()})
			return
		}
		json.NewEncoder(w).Encode(list)
		return
	}

	// Memory fallback
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

	sort.Slice(list, func(i, j int) bool { return list[i].CreatedAt > list[j].CreatedAt })
	json.NewEncoder(w).Encode(list)
}

func (h *digitalIdentityHandler) deleteContainer(w http.ResponseWriter, r *http.Request, containerID string) {
	// PG path
	if h.pg() {
		// Mark as destroyed in PG
		now := time.Now().UTC().Format(time.RFC3339)
		_, _ = h.pool.Exec(r.Context(),
			`UPDATE sandbox_containers SET status='destroyed', stopped_at=$1, updated_at=$1 WHERE id=$2`,
			now, containerID)
		if err := h.deleteContainerPG(r, containerID); err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "pg delete failed"})
			return
		}
		sandboxLifecycle.WithLabelValues("destroyed").Inc()
		w.WriteHeader(http.StatusNoContent)
		return
	}

	// Memory fallback
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
}

func (h *digitalIdentityHandler) getExecLogs(w http.ResponseWriter, r *http.Request, containerID string) {
	// PG path
	if h.pg() {
		logs, err := h.getExecLogsPG(r, containerID)
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "pg logs failed: " + err.Error()})
			return
		}
		json.NewEncoder(w).Encode(logs)
		return
	}

	// Memory fallback
	h.mu.RLock()
	logs, _ := h.execLogs[containerID]
	h.mu.RUnlock()
	json.NewEncoder(w).Encode(logs)
}

func (h *digitalIdentityHandler) containerAction(w http.ResponseWriter, r *http.Request, containerID, action string) {
	// Shared exec logic
	if action == "exec" {
		h.containerExec(w, r, containerID)
		return
	}

	// PG path for start/stop
	if h.pg() {
		c, err := h.getContainerPG(r, containerID)
		if err != nil {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "container not found"})
			return
		}

		switch action {
		case "start":
			c.Status = "starting"
			c.StartedAt = time.Now().UTC().Format(time.RFC3339)
			if err := h.updateContainerStatusPG(r, containerID, "starting"); err != nil {
				log.Printf("digital-identity: pg update status failed: %v", err)
			}
			sandboxLifecycle.WithLabelValues("starting").Inc()

			go func(containerID string, maxRuntime int) {
				time.Sleep(300 * time.Millisecond)
				now := time.Now().UTC()
				_, _ = h.pool.Exec(context.Background(),
					`UPDATE sandbox_containers SET status='running', started_at=$1, updated_at=$1 WHERE id=$2`,
					now, containerID)
				sandboxLifecycle.WithLabelValues("running").Inc()

				h.publishEvent(events.EventSandboxContainerStarted, "", "", map[string]any{
					"container_id": containerID, "status": "running",
				})

				time.Sleep(time.Duration(maxRuntime) * time.Second)
				now2 := time.Now().UTC()
				_, _ = h.pool.Exec(context.Background(),
					`UPDATE sandbox_containers SET status='stopped', stopped_at=$1, updated_at=$1 WHERE id=$2 AND status='running'`,
					now2, containerID)
				sandboxLifecycle.WithLabelValues("stopped").Inc()
			}(containerID, c.MaxRuntimeS)

			json.NewEncoder(w).Encode(c)
			return

		case "stop":
			if err := h.updateContainerStatusPG(r, containerID, "stopped"); err != nil {
				log.Printf("digital-identity: pg stop failed: %v", err)
			}
			c.Status = "stopped"
			c.StoppedAt = time.Now().UTC().Format(time.RFC3339)
			c.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
			sandboxLifecycle.WithLabelValues("stopped").Inc()

			h.publishEvent(events.EventSandboxContainerStopped, "", "", map[string]any{
				"container_id": containerID, "status": "stopped",
			})
			json.NewEncoder(w).Encode(c)
			return

		default:
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "unknown action: " + action})
			return
		}
	}

	// Memory fallback
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
		sandboxLifecycle.WithLabelValues("starting").Inc()

		go func(containerID string) {
			time.Sleep(300 * time.Millisecond)
			h.mu.Lock()
			if cc, ok2 := h.containers[containerID]; ok2 {
				cc.Status = "running"
				cc.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
			}
			h.mu.Unlock()
			sandboxLifecycle.WithLabelValues("running").Inc()

			h.publishEvent(events.EventSandboxContainerStarted, "", "", map[string]any{
				"container_id": containerID, "status": "running",
			})

			time.Sleep(time.Duration(c.MaxRuntimeS) * time.Second)
			h.mu.Lock()
			if cc, ok2 := h.containers[containerID]; ok2 && cc.Status == "running" {
				cc.Status = "stopped"
				cc.StoppedAt = time.Now().UTC().Format(time.RFC3339)
				cc.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
			}
			h.mu.Unlock()
			sandboxLifecycle.WithLabelValues("stopped").Inc()
		}(containerID)

		json.NewEncoder(w).Encode(c)
		return

	case "stop":
		c.Status = "stopped"
		now := time.Now().UTC().Format(time.RFC3339)
		c.StoppedAt = now
		c.UpdatedAt = now
		h.mu.Unlock()
		sandboxLifecycle.WithLabelValues("stopped").Inc()

		h.publishEvent(events.EventSandboxContainerStopped, "", "", map[string]any{
			"container_id": containerID, "status": "stopped",
		})
		json.NewEncoder(w).Encode(c)
		return

	default:
		h.mu.Unlock()
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "unknown action: " + action})
		return
	}
}

func (h *digitalIdentityHandler) containerExec(w http.ResponseWriter, r *http.Request, containerID string) {
	var req struct {
		Command string `json:"command"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Command == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "command is required"})
		return
	}

	execLog := SandboxExecLog{
		ID:          "exec-" + randomSuffix(),
		ContainerID: containerID,
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

	// PG path
	if h.pg() {
		// Fetch container for metadata
		c, err := h.getContainerPG(r, containerID)
		if err != nil {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "container not found"})
			return
		}
		execLog.AgentID = c.AgentID
		execLog.TenantID = c.TenantID

		if err := h.insertExecLogPG(r, execLog); err != nil {
			log.Printf("digital-identity: pg exec log insert failed: %v", err)
		}

		h.publishEvent(events.EventSandboxExecCompleted, "", "", map[string]any{
			"container_id": containerID, "command": req.Command, "exit_code": execLog.ExitCode,
		})

		json.NewEncoder(w).Encode(execLog)
		return
	}

	// Memory fallback
	h.mu.Lock()
	if c, ok := h.containers[containerID]; ok {
		execLog.AgentID = c.AgentID
		execLog.TenantID = c.TenantID
	}
	h.execLogs[containerID] = append(h.execLogs[containerID], execLog)
	if len(h.execLogs[containerID]) > 200 {
		h.execLogs[containerID] = h.execLogs[containerID][len(h.execLogs[containerID])-200:]
	}
	h.mu.Unlock()

	h.publishEvent(events.EventSandboxExecCompleted, "", "", map[string]any{
		"container_id": containerID, "command": req.Command, "exit_code": execLog.ExitCode,
	})

	json.NewEncoder(w).Encode(execLog)
}

// ── Sandbox Stats ──────────────────────────────────────────────────────

func (h *digitalIdentityHandler) serveSandboxStats(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Content-Type", "application/json")

	// PG path
	if h.pg() {
		stats, err := h.computeSandboxStatsPG(r)
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "pg stats failed"})
			return
		}
		json.NewEncoder(w).Encode(stats)
		return
	}

	// Memory fallback
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

	for _, execLogs := range h.execLogs {
		stats.TotalExecs += len(execLogs)
		for _, l := range execLogs {
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
