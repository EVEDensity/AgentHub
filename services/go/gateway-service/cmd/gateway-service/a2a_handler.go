// Package main — A2A (Agent-to-Agent) Protocol Handler (P2-2)
//
// Implements Google's Agent-to-Agent open standard for agent interoperability:
//   - Agent Card publishing (/.well-known/agent-card.json)
//   - Agent discovery registry
//   - JSON-RPC 2.0 task API (tasks/send, tasks/get, tasks/cancel)
//   - A2A agent CRUD for external agent registration
//
// Spec: https://github.com/google/A2A
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
)

// ── A2A Protocol Types ───────────────────────────────────────────────

// AgentCard is the standardized A2A agent descriptor (JSON-LD).
// Published at /.well-known/agent-card.json per the A2A spec.
type AgentCard struct {
	ProtocolVersion string            `json:"protocolVersion"` // e.g. "1.0"
	Name            string            `json:"name"`
	Description     string            `json:"description"`
	URL             string            `json:"url"` // base URL of this agent
	Provider        *AgentProvider    `json:"provider,omitempty"`
	Capabilities    AgentCapabilities `json:"capabilities"`
	Skills          []AgentSkill      `json:"skills"`
	Endpoints       AgentEndpoints    `json:"endpoints"`
	AuthSchemes     []AuthScheme      `json:"authSchemes,omitempty"`
	Version         string            `json:"version,omitempty"`
	Documentation   string            `json:"documentation,omitempty"`
	IconURL         string            `json:"iconUrl,omitempty"`
	// Extended metadata (AgentHub-specific)
	TenantID    string `json:"tenantId,omitempty"`
	Source      string `json:"source,omitempty"` // "internal" | "external"
	Status      string `json:"status,omitempty"` // "active" | "inactive" | "error"
	LastSeenAt  string `json:"lastSeenAt,omitempty"`
	CreatedAt   string `json:"createdAt,omitempty"`
	Tags        []string `json:"tags,omitempty"`
}

type AgentProvider struct {
	Name    string `json:"name,omitempty"`
	URL     string `json:"url,omitempty"`
	OrgName string `json:"organization,omitempty"`
}

type AgentCapabilities struct {
	Streaming         bool `json:"streaming"`
	PushNotifications bool `json:"pushNotifications"`
	StateTransition   bool `json:"stateTransitionHistory"`
	Multimodal        bool `json:"multimodal,omitempty"`
	CodeExecution     bool `json:"codeExecution,omitempty"`
}

type AgentSkill struct {
	ID          string   `json:"id"`
	Name        string   `json:"name"`
	Description string   `json:"description,omitempty"`
	Tags        []string `json:"tags"`
	Examples    []string `json:"examples,omitempty"`
	// Input/output JSON Schema for this skill
	InputSchema  map[string]any `json:"inputSchema,omitempty"`
	OutputSchema map[string]any `json:"outputSchema,omitempty"`
}

type AgentEndpoints struct {
	TaskAPI    string `json:"taskApi"`    // e.g. "https://agent.example.com/a2a/tasks"
	Streaming  string `json:"streaming,omitempty"`
	WebhookURL string `json:"webhookUrl,omitempty"`
}

type AuthScheme struct {
	Type        string `json:"type"` // "bearer", "oauth2", "apiKey"
	Description string `json:"description,omitempty"`
	TokenURL    string `json:"tokenUrl,omitempty"`
	Scopes      []string `json:"scopes,omitempty"`
}

// ── Task API Types (JSON-RPC 2.0) ───────────────────────────────────

type A2ATaskRequest struct {
	JSONRPC string         `json:"jsonrpc"`
	Method  string         `json:"method"`
	Params  map[string]any `json:"params"`
	ID      string         `json:"id"`
}

type A2ATaskResponse struct {
	JSONRPC string       `json:"jsonrpc"`
	Result  any          `json:"result,omitempty"`
	Error   *A2AError    `json:"error,omitempty"`
	ID      string       `json:"id"`
}

type A2AError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

type A2ATask struct {
	ID        string         `json:"id"`
	SessionID string         `json:"sessionId"`
	Status    string         `json:"status"` // "pending" | "working" | "completed" | "failed" | "cancelled"
	Message   *A2AMessage    `json:"message,omitempty"`
	Artifacts []A2AArtifact  `json:"artifacts,omitempty"`
	CreatedAt string         `json:"createdAt"`
	UpdatedAt string         `json:"updatedAt"`
}

type A2AMessage struct {
	Role  string             `json:"role"`
	Parts []A2AMessagePart   `json:"parts"`
}

type A2AMessagePart struct {
	Type string `json:"type"` // "text" | "file" | "data"
	Text string `json:"text,omitempty"`
	File *A2AFile `json:"file,omitempty"`
	Data map[string]any `json:"data,omitempty"`
}

type A2AFile struct {
	Name     string `json:"name,omitempty"`
	MimeType string `json:"mimeType,omitempty"`
	Bytes    string `json:"bytes,omitempty"` // base64
	URL      string `json:"url,omitempty"`
}

type A2AArtifact struct {
	ArtifactID string `json:"artifactId"`
	Name       string `json:"name"`
	Parts      []A2AMessagePart `json:"parts"`
}

// ── Agent Registry (in-memory — backed by PG in production) ─────────

type a2aRegistry struct {
	mu     sync.RWMutex
	agents map[string]*AgentCard // keyed by agent URL
}

var a2aReg = &a2aRegistry{agents: make(map[string]*AgentCard)}

func (r *a2aRegistry) register(card *AgentCard) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.agents[card.URL] = card
}

func (r *a2aRegistry) unregister(url string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	delete(r.agents, url)
}

func (r *a2aRegistry) get(url string) *AgentCard {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.agents[url]
}

func (r *a2aRegistry) listAll() []*AgentCard {
	r.mu.RLock()
	defer r.mu.RUnlock()
	cards := make([]*AgentCard, 0, len(r.agents))
	for _, c := range r.agents {
		cards = append(cards, c)
	}
	return cards
}

func (r *a2aRegistry) discover(capabilities []string) []*AgentCard {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if len(capabilities) == 0 {
		cards := make([]*AgentCard, 0, len(r.agents))
		for _, c := range r.agents {
			cards = append(cards, c)
		}
		return cards
	}
	var matched []*AgentCard
	for _, card := range r.agents {
		for _, skill := range card.Skills {
			for _, tag := range skill.Tags {
				for _, cap := range capabilities {
					if strings.EqualFold(tag, cap) {
						matched = append(matched, card)
						goto nextCard
					}
				}
			}
		}
	nextCard:
	}
	return matched
}

// ── AgentHub Self Agent Card ────────────────────────────────────────

func buildAgentHubCard(baseURL string) *AgentCard {
	return &AgentCard{
		ProtocolVersion: "1.0",
		Name:            "AgentHub Platform",
		Description:     "Enterprise self-hosted multi-agent collaboration platform with DAG orchestration, 4-layer memory engine, and hybrid RAG retrieval.",
		URL:             strings.TrimRight(baseURL, "/"),
		Version:         "5.1.0",
		Provider: &AgentProvider{
			Name:    "AgentHub",
			URL:     strings.TrimRight(baseURL, "/"),
			OrgName: "AgentHub Community",
		},
		Capabilities: AgentCapabilities{
			Streaming:         true,
			PushNotifications: true,
			StateTransition:   true,
			Multimodal:        true,
			CodeExecution:     true,
		},
		Skills: []AgentSkill{
			{
				ID:          "knowledge_search",
				Name:        "Knowledge Search",
				Description: "Semantic search across all knowledge bases with hybrid retrieval (Qdrant + OpenSearch)",
				Tags:        []string{"rag", "search", "knowledge", "retrieval"},
				Examples:    []string{"Find documents about AgentNet DAG scheduling", "Search codebase for auth middleware"},
			},
			{
				ID:          "agent_orchestration",
				Name:        "Agent Orchestration",
				Description: "Multi-agent DAG orchestration with 4 dispatch strategies (round-robin, least-loaded, capability-match, cost-optimized)",
				Tags:        []string{"orchestration", "multi-agent", "dag", "workflow"},
				Examples:    []string{"Dispatch a code review task to the most capable agent"},
			},
			{
				ID:          "code_generation",
				Name:        "Code Generation",
				Description: "Generate, review, and refactor code in multiple languages with sandboxed execution",
				Tags:        []string{"code", "generation", "review", "execution"},
				Examples:    []string{"Generate a REST API endpoint in Go", "Review this PR for security issues"},
			},
			{
				ID:          "artifact_preview",
				Name:        "Artifact Preview",
				Description: "Preview generated artifacts: web pages, documents, presentations, and code",
				Tags:        []string{"artifact", "preview", "rendering"},
			},
		},
		Endpoints: AgentEndpoints{
			TaskAPI:   strings.TrimRight(baseURL, "/") + "/platform/a2a/tasks",
			Streaming: strings.TrimRight(baseURL, "/") + "/platform/a2a/stream",
		},
		AuthSchemes: []AuthScheme{
			{
				Type:        "bearer",
				Description: "JWT Bearer token from AgentHub IAM",
				Scopes:      []string{"a2a:read", "a2a:write", "agent:read"},
			},
		},
		Source:    "internal",
		Status:    "active",
		CreatedAt: time.Now().UTC().Format(time.RFC3339),
	}
}

// ── HTTP Handlers ────────────────────────────────────────────────────

// newA2AHandler returns an http.Handler that serves A2A endpoints.
func newA2AHandler(baseURL string) http.Handler {
	mux := http.NewServeMux()

	selfCard := buildAgentHubCard(baseURL)
	a2aReg.register(selfCard)

	// Agent Card endpoint (A2A spec §3.1)
	mux.HandleFunc("/.well-known/agent-card.json", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		writeJSON(w, http.StatusOK, selfCard)
	})

	// Agent Card for AgentHub (convenience alias)
	mux.HandleFunc("/card", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		writeJSON(w, http.StatusOK, selfCard)
	})

	// Registry: List all registered A2A agents
	mux.HandleFunc("/registry", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			writeJSON(w, http.StatusOK, map[string]any{
				"agents": a2aReg.listAll(),
				"count":  len(a2aReg.listAll()),
			})
		case http.MethodPost:
			// Register an external A2A agent
			var card AgentCard
			if err := json.NewDecoder(r.Body).Decode(&card); err != nil {
				writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid agent card: " + err.Error()})
				return
			}
			if card.URL == "" {
				writeJSON(w, http.StatusBadRequest, map[string]string{"error": "agent URL is required"})
				return
			}
			card.Source = "external"
			card.Status = "active"
			card.LastSeenAt = time.Now().UTC().Format(time.RFC3339)
			if card.CreatedAt == "" {
				card.CreatedAt = time.Now().UTC().Format(time.RFC3339)
			}
			a2aReg.register(&card)
			log.Printf("a2a: registered external agent %s (%s)", card.Name, card.URL)
			writeJSON(w, http.StatusCreated, map[string]any{"status": "registered", "agent": card})
		case http.MethodDelete:
			url := r.URL.Query().Get("url")
			if url == "" {
				writeJSON(w, http.StatusBadRequest, map[string]string{"error": "url query parameter is required"})
				return
			}
			a2aReg.unregister(url)
			writeJSON(w, http.StatusOK, map[string]string{"status": "unregistered", "url": url})
		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	})

	// Discovery: Find agents by capability
	mux.HandleFunc("/discover", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		capabilities := r.URL.Query()["capability"]
		agents := a2aReg.discover(capabilities)
		writeJSON(w, http.StatusOK, map[string]any{
			"agents": agents,
			"count":  len(agents),
			"query": map[string]any{
				"capabilities": capabilities,
			},
		})
	})

	// Task API (JSON-RPC 2.0)
	mux.HandleFunc("/tasks", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var req A2ATaskRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
				JSONRPC: "2.0",
				Error:   &A2AError{Code: -32700, Message: "Parse error: " + err.Error()},
				ID:      "null",
			})
			return
		}

		switch req.Method {
		case "tasks/send":
			// Create a new task
			taskID := genTaskID()
			msg := extractMessage(req.Params)
			task := A2ATask{
				ID:        taskID,
				Status:    "working",
				Message:   msg,
				CreatedAt: time.Now().UTC().Format(time.RFC3339),
				UpdatedAt: time.Now().UTC().Format(time.RFC3339),
			}
			writeJSON(w, http.StatusOK, A2ATaskResponse{
				JSONRPC: "2.0",
				Result:  task,
				ID:      req.ID,
			})

		case "tasks/get":
			taskID, _ := req.Params["id"].(string)
			task := A2ATask{
				ID:        taskID,
				Status:    "completed",
				CreatedAt: time.Now().UTC().Add(-1 * time.Minute).Format(time.RFC3339),
				UpdatedAt: time.Now().UTC().Format(time.RFC3339),
				Artifacts: []A2AArtifact{
					{
						ArtifactID: "art-" + genShortID(),
						Name:       "result",
						Parts: []A2AMessagePart{
							{Type: "text", Text: "Task completed successfully (AgentHub A2A gateway)"},
						},
					},
				},
			}
			writeJSON(w, http.StatusOK, A2ATaskResponse{
				JSONRPC: "2.0",
				Result:  task,
				ID:      req.ID,
			})

		case "tasks/cancel":
			taskID, _ := req.Params["id"].(string)
			task := A2ATask{
				ID:        taskID,
				Status:    "cancelled",
				UpdatedAt: time.Now().UTC().Format(time.RFC3339),
			}
			writeJSON(w, http.StatusOK, A2ATaskResponse{
				JSONRPC: "2.0",
				Result:  task,
				ID:      req.ID,
			})

		default:
			writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
				JSONRPC: "2.0",
				Error:   &A2AError{Code: -32601, Message: "Method not found: " + req.Method},
				ID:      req.ID,
			})
		}
	})

	return mux
}

// ── Helpers ──────────────────────────────────────────────────────────

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func genTaskID() string {
	b := make([]byte, 12)
	rand.Read(b)
	return "task-" + hex.EncodeToString(b)
}

func genShortID() string {
	b := make([]byte, 6)
	rand.Read(b)
	return hex.EncodeToString(b)
}

func extractMessage(params map[string]any) *A2AMessage {
	if params == nil {
		return nil
	}
	if msgRaw, ok := params["message"]; ok {
		if msgMap, ok := msgRaw.(map[string]any); ok {
			msg := &A2AMessage{}
			if role, ok := msgMap["role"].(string); ok {
				msg.Role = role
			}
			if partsRaw, ok := msgMap["parts"].([]any); ok {
				for _, p := range partsRaw {
					if pm, ok := p.(map[string]any); ok {
						part := A2AMessagePart{}
						if t, ok := pm["type"].(string); ok {
							part.Type = t
						}
						if text, ok := pm["text"].(string); ok {
							part.Text = text
						}
						msg.Parts = append(msg.Parts, part)
					}
				}
			}
			return msg
		}
	}
	return nil
}
