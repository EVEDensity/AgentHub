package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
)

// ── Channel Adapter Interface ─────────────────────────────────────

// ChannelAdapter converts inbound messages from external IM platforms
// into AgentHub NATS envelopes and (optionally) formats outbound replies.
type ChannelAdapter interface {
	// Name returns the adapter identifier (e.g. "feishu", "wecom").
	Name() string
	// VerifyRequest checks platform-specific signatures/tokens.
	VerifyRequest(r *http.Request, config ChannelConfig) error
	// ParseMessage extracts text, sender, and metadata from the webhook payload.
	ParseMessage(r *http.Request) (*ChannelMessage, error)
}

type ChannelMessage struct {
	Platform    string         `json:"platform"`    // feishu, wecom, slack
	ChatID      string         `json:"chat_id"`     // group or user chat ID
	SenderID    string         `json:"sender_id"`   // platform user ID
	SenderName  string         `json:"sender_name"` // display name
	Content     string         `json:"content"`     // message text
	MessageType string         `json:"message_type"` // text, image, file, etc.
	Timestamp   string         `json:"timestamp"`
	Raw         map[string]any `json:"raw,omitempty"`
	ThreadID    string         `json:"thread_id,omitempty"`
}

type ChannelConfig struct {
	Platform      string `json:"platform"`
	WebhookURL    string `json:"webhook_url"`
	VerifyToken   string `json:"verify_token"`
	SigningSecret string `json:"signing_secret"`
	Enabled       bool   `json:"enabled"`
	AgentID       string `json:"agent_id"` // default agent to invoke
	BotName       string `json:"bot_name"`
}

// ── Feishu Adapter ────────────────────────────────────────────────

type feishuAdapter struct{}

func (a *feishuAdapter) Name() string { return "feishu" }

func (a *feishuAdapter) VerifyRequest(r *http.Request, config ChannelConfig) error {
	// Feishu event subscription verification (URL challenge)
	// Feishu sends a POST with a "challenge" field on URL verification
	if r.Method == http.MethodPost {
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			return err
		}
		// Re-create body for downstream parsing
		r.Body.Close()
		// If this is a URL verification challenge, the caller should
		// respond with the challenge token directly (handled in ServeHTTP)
		return nil
	}
	return nil
}

func (a *feishuAdapter) ParseMessage(r *http.Request) (*ChannelMessage, error) {
	var payload map[string]any
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		return nil, err
	}

	// Feishu event v1 format
	if header, ok := payload["header"].(map[string]any); ok {
		eventType, _ := header["event_type"].(string)
		if eventType == "im.message.receive_v1" {
			if event, ok := payload["event"].(map[string]any); ok {
				if msg, ok := event["message"].(map[string]any); ok {
					content, _ := msg["content"].(string)
					// Content is JSON-encoded string in Feishu
					var contentObj map[string]any
					if err := json.Unmarshal([]byte(content), &contentObj); err == nil {
						if text, ok := contentObj["text"]; ok {
							content = text.(string)
						}
					}
					chatID, _ := msg["chat_id"].(string)

					var senderID string
					if sender, ok := event["sender"].(map[string]any); ok {
						senderID, _ = sender["sender_id"].(map[string]any)["open_id"].(string)
						// senderName will come from contact API in production
					}

					return &ChannelMessage{
						Platform:    "feishu",
						ChatID:      chatID,
						SenderID:    senderID,
						Content:     content,
						MessageType: "text",
						Timestamp:   time.Now().UTC().Format(time.RFC3339),
						Raw:         payload,
					}, nil
				}
			}
		}
	}

	// Simplified: treat unknown field as text-containing JSON
	content, _ := payload["text"].(string)
	if content == "" {
		content, _ = payload["content"].(string)
	}

	return &ChannelMessage{
		Platform:  "feishu",
		Content:   content,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
		Raw:       payload,
	}, nil
}

// ── WeCom (企业微信) Adapter ───────────────────────────────────────

type wecomAdapter struct{}

func (a *wecomAdapter) Name() string { return "wecom" }

func (a *wecomAdapter) VerifyRequest(r *http.Request, config ChannelConfig) error {
	// WeCom uses msg_signature + timestamp + nonce verification
	// For MVP, just accept the request
	return nil
}

func (a *wecomAdapter) ParseMessage(r *http.Request) (*ChannelMessage, error) {
	var payload map[string]any
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		return nil, err
	}

	// WeCom bot callback format: {"msgtype":"text","text":{"content":"..."},"from":{"userid":"..."}}
	msgType, _ := payload["msgtype"].(string)
	content := ""
	if text, ok := payload["text"].(map[string]any); ok {
		content, _ = text["content"].(string)
	}

	var senderID, senderName string
	if from, ok := payload["from"].(map[string]any); ok {
		senderID, _ = from["userid"].(string)
		senderName, _ = from["name"].(string)
	}
	chatID, _ := payload["chatid"].(string)

	return &ChannelMessage{
		Platform:    "wecom",
		ChatID:      chatID,
		SenderID:    senderID,
		SenderName:  senderName,
		Content:     content,
		MessageType: msgType,
		Timestamp:   time.Now().UTC().Format(time.RFC3339),
		Raw:         payload,
	}, nil
}

// ── Channel Connector Service ──────────────────────────────────────

type channelConnector struct {
	mu       sync.RWMutex
	adapters map[string]ChannelAdapter
	configs  map[string]ChannelConfig // platform -> config
	bus      *eventbus.Client
}

func newChannelConnector(bus *eventbus.Client) *channelConnector {
	cc := &channelConnector{
		adapters: make(map[string]ChannelAdapter),
		configs:  make(map[string]ChannelConfig),
		bus:      bus,
	}
	cc.adapters["feishu"] = &feishuAdapter{}
	cc.adapters["wecom"] = &wecomAdapter{}
	return cc
}

func (cc *channelConnector) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	rel := strings.TrimPrefix(r.URL.Path, "/platform/channels")
	rel = strings.TrimPrefix(rel, "/")

	// Channel management endpoints
	switch {
	case rel == "" && r.Method == http.MethodGet:
		cc.listConfigs(w, r)
	case rel == "" && r.Method == http.MethodPost:
		cc.saveConfig(w, r)
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodDelete:
		cc.deleteConfig(w, r, rel)
	case strings.HasSuffix(rel, "/webhook") && r.Method == http.MethodPost:
		platform := strings.TrimSuffix(rel, "/webhook")
		cc.handleWebhook(w, r, platform)
	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

func (cc *channelConnector) listConfigs(w http.ResponseWriter, _ *http.Request) {
	cc.mu.RLock()
	defer cc.mu.RUnlock()

	configs := make([]ChannelConfig, 0, len(cc.configs))
	for _, c := range cc.configs {
		c.SigningSecret = "" // never expose
		configs = append(configs, c)
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"channels": configs})
}

func (cc *channelConnector) saveConfig(w http.ResponseWriter, r *http.Request) {
	var cfg ChannelConfig
	if err := json.NewDecoder(r.Body).Decode(&cfg); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if cfg.Platform == "" {
		http.Error(w, `{"error":"platform is required"}`, http.StatusBadRequest)
		return
	}

	cc.mu.Lock()
	cc.configs[cfg.Platform] = cfg
	cc.mu.Unlock()

	log.Printf("channel config saved: platform=%s enabled=%v", cfg.Platform, cfg.Enabled)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "saved"})
}

func (cc *channelConnector) deleteConfig(w http.ResponseWriter, _ *http.Request, platform string) {
	cc.mu.Lock()
	delete(cc.configs, platform)
	cc.mu.Unlock()

	log.Printf("channel config deleted: platform=%s", platform)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
}

func (cc *channelConnector) handleWebhook(w http.ResponseWriter, r *http.Request, platform string) {
	cc.mu.RLock()
	cfg, cfgOk := cc.configs[platform]
	adapter, adpOk := cc.adapters[platform]
	cc.mu.RUnlock()

	if !cfgOk || !adpOk {
		http.Error(w, `{"error":"channel not configured"}`, http.StatusNotFound)
		return
	}
	if !cfg.Enabled {
		http.Error(w, `{"error":"channel disabled"}`, http.StatusServiceUnavailable)
		return
	}

	// Feishu URL verification (challenge)
	if platform == "feishu" {
		var body map[string]any
		// Read body for challenge check (body must be re-readable)
		if err := json.NewDecoder(r.Body).Decode(&body); err == nil {
			if challenge, ok := body["challenge"]; ok {
				_ = json.NewEncoder(w).Encode(map[string]any{"challenge": challenge})
				return
			}
		}
	}

	// Parse inbound message
	msg, err := adapter.ParseMessage(r)
	if err != nil {
		log.Printf("channel %s parse error: %v", platform, err)
		http.Error(w, `{"error":"parse failed"}`, http.StatusBadRequest)
		return
	}
	if msg.Content == "" {
		// Empty message (e.g., file share, sticker) — acknowledge silently
		_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
		return
	}

	// Publish to NATS for agent processing
	agentID := cfg.AgentID
	if agentID == "" {
		agentID = "default"
	}

	tenantID := "channel-" + platform
	sessionID := msg.ChatID
	traceID := "ch-" + platform + "-" + randomSuffix()

	event := events.NewEnvelope(
		events.EventSessionMessageReceived,
		tenantID,
		sessionID,
		traceID,
		events.Producer{Service: "gateway-service", Instance: getenv("HOSTNAME", "local")},
		map[string]any{
			"content":     msg.Content,
			"agent_id":    agentID,
			"source":      "channel",
			"platform":    msg.Platform,
			"chat_id":     msg.ChatID,
			"sender_id":   msg.SenderID,
			"sender_name": msg.SenderName,
			"channel_raw": msg.Raw,
		},
	)
	event.EventID = "ch-evt-" + randomSuffix()
	event.Routing = &events.Routing{
		Channel:      "channel",
		PartitionKey: sessionID,
		Priority:     events.PriorityNormal,
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	if err := cc.bus.PublishEnvelope(ctx, eventbus.SessionEventsSubject, event); err != nil {
		log.Printf("channel %s publish error: %v", platform, err)
		http.Error(w, `{"error":"publish failed"}`, http.StatusBadGateway)
		return
	}

	log.Printf("channel %s: message published session=%s sender=%s", platform, sessionID, msg.SenderID)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

// ── HMAC helper ────────────────────────────────────────────────────

func computeHMAC(key, data string) string {
	mac := hmac.New(sha256.New, []byte(key))
	mac.Write([]byte(data))
	return base64.StdEncoding.EncodeToString(mac.Sum(nil))
}
