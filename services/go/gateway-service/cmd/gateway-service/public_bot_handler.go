package main

import (
	"encoding/json"
	"net/http"
	"strings"
)

// ── Public Bot Config ──────────────────────────────────────────────────
//
// GET /api/public/bots/:botId → returns the public-facing configuration for a
// bot/agent so the standalone web app page can render custom branding.
//
// The bot config is stored inside the agent's metadata JSON field in the
// platform_agents table. When no custom config is set, sensible defaults are
// returned so every agent is embeddable out of the box.

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
func handlePublicBotConfig(w http.ResponseWriter, r *http.Request) {
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
	cfg := loadPublicBotConfig(botID)

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

// loadPublicBotConfig returns the public config for a bot. It first checks the
// global agentVersionHandler's in-memory store, then falls back to defaults.
func loadPublicBotConfig(botID string) publicBotConfig {
	cfg := publicBotConfig{
		BotID:          botID,
		Name:           botID,
		WelcomeMessage: "你好！我是 AI 助手，有什么可以帮你的？",
		Placeholder:    "输入消息...",
		ThemeColor:     "#6366f1",
		PoweredBy:      "AgentHub",
	}

	// Try to resolve from the global agent version handler
	if globalAgentVersionHandler != nil {
		versions, ok := globalAgentVersionHandler.store[botID]

		if ok && len(versions) > 0 {
			latest := versions[0] // versions are stored desc by version number
			snapshot := latest.Snapshot

			if name, ok := snapshot["name"].(string); ok && name != "" {
				cfg.Name = name
			}
			if meta, ok := snapshot["metadata"].(map[string]interface{}); ok {
				if wm, ok := meta["welcomeMessage"].(string); ok && wm != "" {
					cfg.WelcomeMessage = wm
				}
				if ph, ok := meta["placeholder"].(string); ok && ph != "" {
					cfg.Placeholder = ph
				}
				if tc, ok := meta["themeColor"].(string); ok && tc != "" {
					cfg.ThemeColor = tc
				}
				if logo, ok := meta["logoUrl"].(string); ok && logo != "" {
					cfg.LogoURL = logo
				}
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

	// Default suggested questions if none configured
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
