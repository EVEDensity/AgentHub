package main

import (
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
)

// ── Tool Definition ─────────────────────────────────────────────────

// ToolDefinition mirrors the frontend ToolDefinition type.
type ToolDefinition struct {
	ID                       string                   `json:"id"`
	Name                     string                   `json:"name"`
	Description              string                   `json:"description"`
	Category                 string                   `json:"category"`
	Icon                     string                   `json:"icon,omitempty"`
	Parameters               []ToolParam              `json:"parameters"`
	ReturnType               string                   `json:"returnType"`
	Examples                 []ToolExample            `json:"examples"`
	RiskLevel                string                   `json:"riskLevel"`
	HandlerType              string                   `json:"handlerType"`
	Enabled                  bool                     `json:"enabled"`
	IsConcurrencySafe        bool                     `json:"isConcurrencySafe"`
	RequiresUserConfirmation bool                     `json:"requiresUserConfirmation"`
	CreatedAt                string                   `json:"createdAt"`
}

type ToolParam struct {
	Name        string   `json:"name"`
	Type        string   `json:"type"`
	Required    bool     `json:"required"`
	Description string   `json:"description"`
	Default     string   `json:"default,omitempty"`
	Enum        []string `json:"enum,omitempty"`
}

type ToolExample struct {
	UserQuestion string                 `json:"user_question"`
	Parameters   map[string]interface{} `json:"parameters"`
}

type ToolBinding struct {
	AgentID string   `json:"agent_id"`
	ToolIDs []string `json:"tool_ids"`
}

// ── 40 Built-in Tools ───────────────────────────────────────────────

func builtinTools() []ToolDefinition {
	return []ToolDefinition{
		// ── Search ──
		{ID: "tool-search-web", Name: "Web Search", Description: "Search the web for current information", Category: "search", Icon: "search", Parameters: []ToolParam{{Name: "query", Type: "string", Required: true, Description: "Search query"}}, ReturnType: "object", Examples: []ToolExample{{UserQuestion: "What's the latest news about AI?", Parameters: map[string]interface{}{"query": "latest AI news 2026"}}}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-search-doc", Name: "Document Search", Description: "Search within knowledge base documents", Category: "search", Icon: "description", Parameters: []ToolParam{{Name: "query", Type: "string", Required: true, Description: "Search query"}, {Name: "collection", Type: "string", Required: false, Description: "Knowledge base collection name"}}, ReturnType: "object", Examples: []ToolExample{{UserQuestion: "Find documents about microservices", Parameters: map[string]interface{}{"query": "microservices architecture"}}}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-search-image", Name: "Image Search", Description: "Search for images by description", Category: "search", Icon: "image_search", Parameters: []ToolParam{{Name: "query", Type: "string", Required: true, Description: "Image description"}}, ReturnType: "object", Examples: []ToolExample{}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},

		// ── File ──
		{ID: "tool-file-read", Name: "Read File", Description: "Read contents of a file from the workspace", Category: "file", Icon: "description", Parameters: []ToolParam{{Name: "path", Type: "string", Required: true, Description: "File path relative to workspace root"}}, ReturnType: "string", Examples: []ToolExample{{UserQuestion: "Show me the contents of README.md", Parameters: map[string]interface{}{"path": "README.md"}}}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-file-write", Name: "Write File", Description: "Write content to a file in the workspace", Category: "file", Icon: "edit", Parameters: []ToolParam{{Name: "path", Type: "string", Required: true, Description: "File path"}, {Name: "content", Type: "string", Required: true, Description: "File content"}}, ReturnType: "object", Examples: []ToolExample{}, RiskLevel: "L2", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: false, RequiresUserConfirmation: true, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-file-list", Name: "List Files", Description: "List files in a directory", Category: "file", Icon: "folder", Parameters: []ToolParam{{Name: "path", Type: "string", Required: false, Description: "Directory path (defaults to workspace root)"}}, ReturnType: "array", Examples: []ToolExample{}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-file-delete", Name: "Delete File", Description: "Delete a file from the workspace", Category: "file", Icon: "delete", Parameters: []ToolParam{{Name: "path", Type: "string", Required: true, Description: "File path to delete"}}, ReturnType: "object", Examples: []ToolExample{}, RiskLevel: "L3", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: false, RequiresUserConfirmation: true, CreatedAt: time.Now().UTC().Format(time.RFC3339)},

		// ── Code ──
		{ID: "tool-code-exec", Name: "Code Execution", Description: "Execute code in a sandboxed environment (Python/JS)", Category: "code", Icon: "code", Parameters: []ToolParam{{Name: "language", Type: "string", Required: true, Description: "Programming language", Enum: []string{"python", "javascript", "bash"}}, {Name: "code", Type: "string", Required: true, Description: "Source code to execute"}}, ReturnType: "object", Examples: []ToolExample{{UserQuestion: "Run this Python code", Parameters: map[string]interface{}{"language": "python", "code": "print('hello world')"}}}, RiskLevel: "L2", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: false, RequiresUserConfirmation: true, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-code-review", Name: "Code Review", Description: "Review code for bugs, style, and security issues", Category: "code", Icon: "rate_review", Parameters: []ToolParam{{Name: "code", Type: "string", Required: true, Description: "Source code to review"}, {Name: "language", Type: "string", Required: false, Description: "Programming language"}}, ReturnType: "object", Examples: []ToolExample{}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-git-diff", Name: "Git Diff", Description: "Show git diff between commits or branches", Category: "code", Icon: "difference", Parameters: []ToolParam{{Name: "base", Type: "string", Required: false, Description: "Base ref (default: HEAD~1)"}, {Name: "head", Type: "string", Required: false, Description: "Head ref (default: HEAD)"}}, ReturnType: "string", Examples: []ToolExample{}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-git-log", Name: "Git Log", Description: "Show git commit history", Category: "code", Icon: "history", Parameters: []ToolParam{{Name: "count", Type: "integer", Required: false, Description: "Number of commits (default: 10)"}}, ReturnType: "array", Examples: []ToolExample{}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},

		// ── Memory ──
		{ID: "tool-memory-recall", Name: "Memory Recall", Description: "Recall past conversations and context", Category: "memory", Icon: "psychology", Parameters: []ToolParam{{Name: "query", Type: "string", Required: true, Description: "What to recall"}, {Name: "limit", Type: "integer", Required: false, Description: "Max results (default: 5)"}}, ReturnType: "array", Examples: []ToolExample{{UserQuestion: "What did we discuss about the database last week?", Parameters: map[string]interface{}{"query": "database discussion"}}}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-memory-save", Name: "Save Memory", Description: "Explicitly save information to agent memory", Category: "memory", Icon: "save", Parameters: []ToolParam{{Name: "content", Type: "string", Required: true, Description: "Information to remember"}, {Name: "tags", Type: "string", Required: false, Description: "Comma-separated tags"}}, ReturnType: "object", Examples: []ToolExample{}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},

		// ── Integration ──
		{ID: "tool-github-api", Name: "GitHub API", Description: "Interact with GitHub repositories (issues, PRs, commits)", Category: "integration", Icon: "link", Parameters: []ToolParam{{Name: "owner", Type: "string", Required: true, Description: "Repository owner"}, {Name: "repo", Type: "string", Required: true, Description: "Repository name"}, {Name: "action", Type: "string", Required: true, Description: "Action to perform", Enum: []string{"list_issues", "create_issue", "list_prs", "get_pr", "list_commits"}}}, ReturnType: "object", Examples: []ToolExample{}, RiskLevel: "L2", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: true, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-gitlab-api", Name: "GitLab API", Description: "Interact with GitLab projects", Category: "integration", Icon: "link", Parameters: []ToolParam{{Name: "project", Type: "string", Required: true, Description: "Project path"}, {Name: "action", Type: "string", Required: true, Description: "Action to perform"}}, ReturnType: "object", Examples: []ToolExample{}, RiskLevel: "L2", HandlerType: "builtin", Enabled: false, IsConcurrencySafe: true, RequiresUserConfirmation: true, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-slack-send", Name: "Send Slack Message", Description: "Send a message to a Slack channel", Category: "integration", Icon: "chat", Parameters: []ToolParam{{Name: "channel", Type: "string", Required: true, Description: "Slack channel"}, {Name: "message", Type: "string", Required: true, Description: "Message text"}}, ReturnType: "object", Examples: []ToolExample{}, RiskLevel: "L2", HandlerType: "builtin", Enabled: false, IsConcurrencySafe: true, RequiresUserConfirmation: true, CreatedAt: time.Now().UTC().Format(time.RFC3339)},

		// ── Browser ──
		{ID: "tool-web-fetch", Name: "Fetch Web Page", Description: "Fetch and extract text content from a URL", Category: "browser", Icon: "language", Parameters: []ToolParam{{Name: "url", Type: "string", Required: true, Description: "URL to fetch"}}, ReturnType: "string", Examples: []ToolExample{{UserQuestion: "What does this page say?", Parameters: map[string]interface{}{"url": "https://example.com"}}}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-web-screenshot", Name: "Web Screenshot", Description: "Take a screenshot of a web page", Category: "browser", Icon: "screenshot", Parameters: []ToolParam{{Name: "url", Type: "string", Required: true, Description: "URL to screenshot"}, {Name: "full_page", Type: "boolean", Required: false, Description: "Capture full page (default: false)"}}, ReturnType: "string", Examples: []ToolExample{}, RiskLevel: "L2", HandlerType: "builtin", Enabled: false, IsConcurrencySafe: false, RequiresUserConfirmation: true, CreatedAt: time.Now().UTC().Format(time.RFC3339)},

		// ── Data ──
		{ID: "tool-sql-query", Name: "SQL Query", Description: "Execute a read-only SQL query against the database", Category: "data", Icon: "database", Parameters: []ToolParam{{Name: "query", Type: "string", Required: true, Description: "SQL SELECT query"}}, ReturnType: "array", Examples: []ToolExample{{UserQuestion: "How many agents are registered?", Parameters: map[string]interface{}{"query": "SELECT count(*) FROM platform_agents"}}}, RiskLevel: "L3", HandlerType: "builtin", Enabled: false, IsConcurrencySafe: true, RequiresUserConfirmation: true, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-chart-gen", Name: "Chart Generator", Description: "Generate charts and visualizations from data", Category: "data", Icon: "bar_chart", Parameters: []ToolParam{{Name: "data", Type: "string", Required: true, Description: "JSON data array"}, {Name: "chart_type", Type: "string", Required: true, Description: "Chart type", Enum: []string{"bar", "line", "pie", "scatter", "area"}}}, ReturnType: "string", Examples: []ToolExample{}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},

		// ── AI ──
		{ID: "tool-text-summarize", Name: "Text Summarization", Description: "Summarize long text into key points", Category: "ai", Icon: "summarize", Parameters: []ToolParam{{Name: "text", Type: "string", Required: true, Description: "Text to summarize"}, {Name: "max_length", Type: "integer", Required: false, Description: "Max summary length in words"}}, ReturnType: "string", Examples: []ToolExample{}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-text-translate", Name: "Text Translation", Description: "Translate text between languages", Category: "ai", Icon: "translate", Parameters: []ToolParam{{Name: "text", Type: "string", Required: true, Description: "Text to translate"}, {Name: "source_lang", Type: "string", Required: false, Description: "Source language (auto-detect if omitted)"}, {Name: "target_lang", Type: "string", Required: true, Description: "Target language", Default: "en"}}, ReturnType: "string", Examples: []ToolExample{}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-text-sentiment", Name: "Sentiment Analysis", Description: "Analyze sentiment and emotion in text", Category: "ai", Icon: "sentiment_satisfied", Parameters: []ToolParam{{Name: "text", Type: "string", Required: true, Description: "Text to analyze"}}, ReturnType: "object", Examples: []ToolExample{}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},

		// ── System ──
		{ID: "tool-system-info", Name: "System Info", Description: "Get system and platform information", Category: "system", Icon: "settings", Parameters: []ToolParam{}, ReturnType: "object", Examples: []ToolExample{}, RiskLevel: "L1", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: false, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
		{ID: "tool-scheduler", Name: "Task Scheduler", Description: "Schedule a task to run at a specific time", Category: "system", Icon: "schedule", Parameters: []ToolParam{{Name: "task", Type: "string", Required: true, Description: "Task description"}, {Name: "cron", Type: "string", Required: true, Description: "Cron expression"}, {Name: "agent_id", Type: "string", Required: false, Description: "Agent to execute the task"}}, ReturnType: "object", Examples: []ToolExample{}, RiskLevel: "L2", HandlerType: "builtin", Enabled: true, IsConcurrencySafe: true, RequiresUserConfirmation: true, CreatedAt: time.Now().UTC().Format(time.RFC3339)},

		// ── Notification ──
		{ID: "tool-email-send", Name: "Send Email", Description: "Send an email via SMTP", Category: "notification", Icon: "email", Parameters: []ToolParam{{Name: "to", Type: "string", Required: true, Description: "Recipient email"}, {Name: "subject", Type: "string", Required: true, Description: "Email subject"}, {Name: "body", Type: "string", Required: true, Description: "Email body (Markdown)"}}, ReturnType: "object", Examples: []ToolExample{}, RiskLevel: "L2", HandlerType: "builtin", Enabled: false, IsConcurrencySafe: true, RequiresUserConfirmation: true, CreatedAt: time.Now().UTC().Format(time.RFC3339)},
	}

	// Total: 24 built-in tools across 8 categories
}

// ── Tool Handler ─────────────────────────────────────────────────────

type toolHandler struct {
	mu       sync.RWMutex
	tools    map[string]ToolDefinition
	bindings map[string][]string // agentID -> toolIDs
}

func newToolHandler() *toolHandler {
	h := &toolHandler{
		tools:    make(map[string]ToolDefinition),
		bindings: make(map[string][]string),
	}
	for _, t := range builtinTools() {
		h.tools[t.ID] = t
	}
	log.Printf("tool handler: %d built-in tools loaded", len(h.tools))
	return h
}

func (h *toolHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	rel := strings.TrimPrefix(r.URL.Path, "/api/admin/tools")
	rel = strings.TrimPrefix(rel, "/")

	// ── Tool bindings ──
	if strings.HasPrefix(rel, "bindings/") {
		agentID := strings.TrimPrefix(rel, "bindings/")
		h.handleBindings(w, r, agentID)
		return
	}

	// ── Tool CRUD ──
	switch {
	case rel == "" && r.Method == http.MethodGet:
		h.listTools(w, r)
	case rel == "" && r.Method == http.MethodPost:
		h.createTool(w, r)
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodGet:
		h.getTool(w, r, rel)
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodPut:
		h.updateTool(w, r, rel)
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodDelete:
		h.deleteTool(w, r, rel)
	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

// ── Tool CRUD ────────────────────────────────────────────────────────

func (h *toolHandler) listTools(w http.ResponseWriter, _ *http.Request) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	tools := make([]ToolDefinition, 0, len(h.tools))
	for _, t := range h.tools {
		tools = append(tools, t)
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"tools": tools})
}

func (h *toolHandler) getTool(w http.ResponseWriter, _ *http.Request, id string) {
	h.mu.RLock()
	t, ok := h.tools[id]
	h.mu.RUnlock()
	if !ok {
		http.Error(w, `{"error":"tool not found"}`, http.StatusNotFound)
		return
	}
	_ = json.NewEncoder(w).Encode(t)
}

func (h *toolHandler) createTool(w http.ResponseWriter, r *http.Request) {
	var t ToolDefinition
	if err := json.NewDecoder(r.Body).Decode(&t); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if t.ID == "" {
		t.ID = "tool-custom-" + randomSuffix()
	}
	if t.CreatedAt == "" {
		t.CreatedAt = time.Now().UTC().Format(time.RFC3339)
	}
	if t.HandlerType == "" {
		t.HandlerType = "custom"
	}

	h.mu.Lock()
	h.tools[t.ID] = t
	h.mu.Unlock()

	log.Printf("tool created: id=%s name=%s", t.ID, t.Name)
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(t)
}

func (h *toolHandler) updateTool(w http.ResponseWriter, r *http.Request, id string) {
	h.mu.Lock()
	existing, ok := h.tools[id]
	if !ok {
		h.mu.Unlock()
		http.Error(w, `{"error":"tool not found"}`, http.StatusNotFound)
		return
	}

	var updates map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&updates); err != nil {
		h.mu.Unlock()
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	// Merge updates (simple field-by-field for MVP)
	if v, ok := updates["name"]; ok {
		existing.Name = v.(string)
	}
	if v, ok := updates["description"]; ok {
		existing.Description = v.(string)
	}
	if v, ok := updates["category"]; ok {
		existing.Category = v.(string)
	}
	if v, ok := updates["enabled"]; ok {
		existing.Enabled = v.(bool)
	}
	if v, ok := updates["riskLevel"]; ok {
		existing.RiskLevel = v.(string)
	}
	if v, ok := updates["requiresUserConfirmation"]; ok {
		existing.RequiresUserConfirmation = v.(bool)
	}

	h.tools[id] = existing
	h.mu.Unlock()

	log.Printf("tool updated: id=%s", id)
	_ = json.NewEncoder(w).Encode(existing)
}

func (h *toolHandler) deleteTool(w http.ResponseWriter, _ *http.Request, id string) {
	h.mu.Lock()
	_, ok := h.tools[id]
	delete(h.tools, id)
	h.mu.Unlock()

	if !ok {
		http.Error(w, `{"error":"tool not found"}`, http.StatusNotFound)
		return
	}
	log.Printf("tool deleted: id=%s", id)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
}

// ── Agent-Tool Bindings ─────────────────────────────────────────────

func (h *toolHandler) handleBindings(w http.ResponseWriter, r *http.Request, agentID string) {
	if agentID == "" {
		http.Error(w, `{"error":"agent_id required"}`, http.StatusBadRequest)
		return
	}

	switch r.Method {
	case http.MethodGet:
		h.mu.RLock()
		toolIDs := h.bindings[agentID]
		h.mu.RUnlock()
		if toolIDs == nil {
			toolIDs = []string{}
		}
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"agent_id": agentID, "tool_ids": toolIDs})
	case http.MethodPut:
		var binding ToolBinding
		if err := json.NewDecoder(r.Body).Decode(&binding); err != nil {
			http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
			return
		}
		h.mu.Lock()
		h.bindings[agentID] = binding.ToolIDs
		h.mu.Unlock()
		log.Printf("tool bindings updated: agent=%s tools=%v", agentID, binding.ToolIDs)
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"agent_id": agentID, "tool_ids": binding.ToolIDs})
	default:
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
	}
}

// (randomSuffix is shared via workspace_handler.go)
