package main

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"

	"github.com/agenthub/platform/shared/db"
)

// ── Public Bot Config ──────────────────────────────────────────────────
//
// GET /api/public/bots/:botId → returns the public-facing configuration for a
// bot/agent so the standalone web app page can render custom branding.
//
// The bot config is resolved from two sources, merged in priority order:
//  1. agent_registry.config JSON column (Python API — admin-configured metadata)
//  2. platform_agent_versions.snapshot["metadata"] (Go version store)
//  3. Hard-coded defaults for everything else

// publicBotConfig is the subset of agent data safe to expose on the public
// internet (no API keys, no internal IDs, no quotas).
type publicBotConfig struct {
	BotID              string   `json:"botId"`
	Name               string   `json:"name"`
	WelcomeMessage     string   `json:"welcomeMessage"`
	Placeholder        string   `json:"placeholder"`
	ThemeColor         string   `json:"themeColor"`
	LogoURL            string   `json:"logoUrl"`
	SuggestedQuestions []string `json:"suggestedQuestions"`
	PoweredBy          string   `json:"poweredBy"`
}

// handlePublicBotConfig serves GET /api/public/bots/{botId}
// Query parameters:
//
//	embed=true  — omit header/footer chrome for iframe embedding
func handlePublicBotConfig(w http.ResponseWriter, r *http.Request, pool *db.Pool) {
	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "GET required"})
		return
	}

	// Extract botId from /api/public/bots/{botId}
	path := strings.TrimPrefix(r.URL.Path, "/api/public/bots/")
	botID := strings.TrimSuffix(path, "/")
	if botID == "" {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "botId is required"})
		return
	}

	// Try to load real agent config from the in-memory agent store.
	// Falls back to defaults if the agent doesn't exist or has no public config.
	cfg := loadPublicBotConfig(botID, pool)

	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
	_ = json.NewEncoder(w).Encode(cfg)
}

// handlePublicBotOptions handles CORS preflight for /api/public/bots/*
func handlePublicBotOptions(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
	w.WriteHeader(http.StatusNoContent)
}

// ── Config Resolution ──────────────────────────────────────────────────

// loadPublicBotConfig returns the public config for a bot. It reads from:
//  1. agent_registry.config → publicConfig (admin-configured via Python API)
//  2. platform_agent_versions.snapshot["metadata"] (Go version store)
//  3. Hard-coded defaults
func loadPublicBotConfig(botID string, pool *db.Pool) publicBotConfig {
	cfg := publicBotConfig{
		BotID:          botID,
		Name:           botID,
		WelcomeMessage: "你好！我是 AI 助手，有什么可以帮你的？",
		Placeholder:    "输入消息...",
		ThemeColor:     "#6366f1",
		PoweredBy:      "AgentHub",
	}

	// ── Source 1: agent_registry.config (Python API metadata) ──────
	if pool != nil {
		var configJSON string
		err := pool.QueryRow(
			context.Background(),
			"SELECT config FROM agent_registry WHERE agent_id=$1 AND config IS NOT NULL AND config != '{}' LIMIT 1",
			botID,
		).Scan(&configJSON)
		if err == nil && configJSON != "" {
			var config map[string]interface{}
			if json.Unmarshal([]byte(configJSON), &config) == nil {
				if pc, ok := config["publicConfig"].(map[string]interface{}); ok {
					if enabled, _ := pc["enabled"].(bool); !enabled {
						// Public share disabled — but still serve defaults
						// (config exists but publicConfig.enabled is false)
					}
					if wm, ok := pc["welcomeMessage"].(string); ok && wm != "" {
						cfg.WelcomeMessage = wm
					}
					if ph, ok := pc["placeholder"].(string); ok && ph != "" {
						cfg.Placeholder = ph
					}
					if tc, ok := pc["themeColor"].(string); ok && tc != "" {
						cfg.ThemeColor = tc
					}
					if logo, ok := pc["logoUrl"].(string); ok && logo != "" {
						cfg.LogoURL = logo
					}
					if sq, ok := pc["suggestedQuestions"].([]interface{}); ok {
						for _, q := range sq {
							if qs, ok := q.(string); ok {
								cfg.SuggestedQuestions = append(cfg.SuggestedQuestions, qs)
							}
						}
					}
				}
			}
		}
	}

	// ── Source 2: platform_agent_versions.snapshot metadata ────────
	if globalAgentVersionHandler != nil {
		versions, ok := globalAgentVersionHandler.store[botID]

		if ok && len(versions) > 0 {
			latest := versions[0] // versions are stored desc by version number
			snapshot := latest.Snapshot

			if name, ok := snapshot["name"].(string); ok && name != "" {
				cfg.Name = name
			}
			if meta, ok := snapshot["metadata"].(map[string]interface{}); ok {
				// Only fill fields NOT already set by agent_registry.config
				if cfg.WelcomeMessage == "你好！我是 AI 助手，有什么可以帮你的？" {
					if wm, ok := meta["welcomeMessage"].(string); ok && wm != "" {
						cfg.WelcomeMessage = wm
					}
				}
				if cfg.Placeholder == "输入消息..." {
					if ph, ok := meta["placeholder"].(string); ok && ph != "" {
						cfg.Placeholder = ph
					}
				}
				if cfg.ThemeColor == "#6366f1" {
					if tc, ok := meta["themeColor"].(string); ok && tc != "" {
						cfg.ThemeColor = tc
					}
				}
				if cfg.LogoURL == "" {
					if logo, ok := meta["logoUrl"].(string); ok && logo != "" {
						cfg.LogoURL = logo
					}
				}
				if len(cfg.SuggestedQuestions) == 0 {
					if sq, ok := meta["suggestedQuestions"].([]interface{}); ok {
						for _, q := range sq {
							if qs, ok := q.(string); ok {
								cfg.SuggestedQuestions = append(cfg.SuggestedQuestions, qs)
							}
						}
					}
				}
			}
		}
	}

	// ── Default suggested questions if none configured ─────────────
	if len(cfg.SuggestedQuestions) == 0 {
		cfg.SuggestedQuestions = []string{
			"你能做什么？",
			"介绍一下你自己",
			"帮我分析一个问题",
		}
	}

	return cfg
}

// ── Global Reference ───────────────────────────────────────────────────
// Set by main.go after agentVersionHandler is created.

var globalAgentVersionHandler *agentVersionHandler
