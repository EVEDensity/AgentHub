package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/agenthub/platform/shared/db"
	"github.com/agenthub/platform/shared/iam"
)

// agentCatalogEntry is the MCP-safe projection of a configured agent. It
// intentionally excludes API keys, provider base URLs, raw config, and avatars.
type agentCatalogEntry struct {
	AgentID      string   `json:"agent_id"`
	DisplayName  string   `json:"display_name"`
	Domain       string   `json:"domain"`
	Status       string   `json:"status"`
	AdapterType  string   `json:"adapter_type"`
	ModelName    string   `json:"model_name"`
	RiskLevel    string   `json:"risk_level"`
	DutyNote     string   `json:"duty_note"`
	Capabilities []string `json:"capabilities"`
}

type agentCatalogListFunc func(context.Context, string) ([]agentCatalogEntry, error)

type agentRegistryHandler struct {
	list agentCatalogListFunc
}

func newAgentRegistryHandler(pool *db.Pool) *agentRegistryHandler {
	return &agentRegistryHandler{
		list: func(ctx context.Context, userID string) ([]agentCatalogEntry, error) {
			return listVisibleAgents(ctx, pool, userID)
		},
	}
}

func (h *agentRegistryHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	principal, ok := iam.FromContext(r.Context())
	if !ok || strings.TrimSpace(principal.TenantID) == "" || strings.TrimSpace(principal.UserID) == "" {
		http.Error(w, "forbidden: tenant and actor context required", http.StatusForbidden)
		return
	}

	agents, err := h.list(r.Context(), principal.UserID)
	if err != nil {
		http.Error(w, "agent registry unavailable", http.StatusBadGateway)
		return
	}
	if agents == nil {
		agents = []agentCatalogEntry{}
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"tenant_id": principal.TenantID,
		"count":     len(agents),
		"agents":    agents,
	})
}

func listVisibleAgents(ctx context.Context, pool *db.Pool, userID string) ([]agentCatalogEntry, error) {
	if pool == nil {
		return nil, fmt.Errorf("agent registry database is unavailable")
	}
	rows, err := pool.Query(ctx, `
		SELECT agent_id, display_name, domain, status, adapter_type,
		       base_model_name, risk_level, duty_note, capability_tags
		FROM (
			SELECT DISTINCT ON (agent_id)
			       agent_id, display_name, domain, status, adapter_type,
			       base_model_name, risk_level, duty_note, capability_tags
			FROM agent_registry
			WHERE user_id = $1 OR user_id = ''
			ORDER BY agent_id, CASE WHEN user_id = $1 THEN 0 ELSE 1 END
		) AS visible_agents
		ORDER BY agent_id`, userID)
	if err != nil {
		return nil, fmt.Errorf("query visible agents: %w", err)
	}
	defer rows.Close()

	agents := make([]agentCatalogEntry, 0)
	for rows.Next() {
		var entry agentCatalogEntry
		var capabilityTags string
		if err := rows.Scan(
			&entry.AgentID,
			&entry.DisplayName,
			&entry.Domain,
			&entry.Status,
			&entry.AdapterType,
			&entry.ModelName,
			&entry.RiskLevel,
			&entry.DutyNote,
			&capabilityTags,
		); err != nil {
			return nil, fmt.Errorf("scan visible agent: %w", err)
		}
		entry.Capabilities = parseCapabilityTags(capabilityTags)
		agents = append(agents, entry)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate visible agents: %w", err)
	}
	return agents, nil
}

func parseCapabilityTags(raw string) []string {
	var capabilities []string
	if err := json.Unmarshal([]byte(raw), &capabilities); err != nil || capabilities == nil {
		return []string{}
	}
	return capabilities
}
