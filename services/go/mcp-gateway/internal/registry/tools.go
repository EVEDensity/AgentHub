// Package registry provides the MCP tool/resource/prompt registries
// and adapters that expose AgentHub platform capabilities as MCP tools.
package registry

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	mcpauth "github.com/agenthub/mcp-gateway/internal/auth"
	"github.com/agenthub/mcp-gateway/internal/protocol"
	"github.com/agenthub/mcp-gateway/internal/transport"
	"github.com/agenthub/platform/shared/iam"
)

var (
	ErrTenantIdentityRequired       = errors.New("authenticated tenant and actor are required")
	ErrDownstreamCredentialRequired = errors.New("authenticated downstream credential is required")
)

// ── Tool Registry ────────────────────────────────────────────────────

// ToolFunc is the function signature for executing a tool.
// It receives the call arguments and returns the tool result.
type ToolFunc func(ctx context.Context, args map[string]any) (*protocol.ToolCallResult, error)

// RegisteredTool pairs a tool definition with its executor.
type RegisteredTool struct {
	Definition protocol.ToolDefinition
	Capability string
	Handler    ToolFunc
}

// RegisteredResource pairs an MCP resource with the single Contract
// capability allowed to read it through the stateless execution boundary.
type RegisteredResource struct {
	Definition protocol.ResourceDefinition
	Capability string
}

// CapabilityMismatchError means a stateless request tried to invoke a tool
// outside the single capability declared by its Contract execution context.
type CapabilityMismatchError struct {
	Tool               string
	DeclaredCapability string
}

func (e *CapabilityMismatchError) Error() string {
	return fmt.Sprintf("tool %q is not available for the declared capability", e.Tool)
}

// ResourceCapabilityMismatchError means a stateless request tried to read a
// resource outside the single capability declared by its Contract context.
type ResourceCapabilityMismatchError struct {
	URI                string
	DeclaredCapability string
}

func (e *ResourceCapabilityMismatchError) Error() string {
	return fmt.Sprintf("resource %q is not available for the declared capability", e.URI)
}

// Registry holds all registered MCP tools, resources, and prompts.
type Registry struct {
	tools     map[string]RegisteredTool
	resources []RegisteredResource
	prompts   []protocol.PromptDefinition

	// AgentHub platform URLs (configurable for dev/prod).
	knowledgeURL string
	gatewayURL   string
}

// New creates a new Registry with AgentHub platform tool adapters pre-registered.
func New(knowledgeURL, gatewayURL string) *Registry {
	if knowledgeURL == "" {
		knowledgeURL = "http://127.0.0.1:8092"
	}
	if gatewayURL == "" {
		gatewayURL = "http://127.0.0.1:8081"
	}

	r := &Registry{
		tools:        make(map[string]RegisteredTool),
		resources:    make([]RegisteredResource, 0),
		prompts:      make([]protocol.PromptDefinition, 0),
		knowledgeURL: knowledgeURL,
		gatewayURL:   gatewayURL,
	}

	r.registerAgentHubTools()
	r.registerAgentHubResources()
	r.registerAgentHubPrompts()
	return r
}

// ── Accessors ────────────────────────────────────────────────────────

// ListTools returns all registered tool definitions.
func (r *Registry) ListTools() []protocol.ToolDefinition {
	defs := make([]protocol.ToolDefinition, 0, len(r.tools))
	for _, t := range r.tools {
		defs = append(defs, t.Definition)
	}
	return defs
}

// CallTool executes a registered tool by name.
func (r *Registry) CallTool(ctx context.Context, name string, args map[string]any) (*protocol.ToolCallResult, error) {
	t, ok := r.tools[name]
	if !ok {
		return nil, fmt.Errorf("tool %q not found", name)
	}
	if request, scoped := transport.RequestContextFromContext(ctx); scoped && request.Capability != t.Capability {
		return nil, &CapabilityMismatchError{
			Tool:               name,
			DeclaredCapability: request.Capability,
		}
	}
	return t.Handler(ctx, args)
}

func (r *Registry) registerTool(tool RegisteredTool) {
	name := strings.TrimSpace(tool.Definition.Name)
	capability := strings.TrimSpace(tool.Capability)
	if name == "" || capability == "" || tool.Handler == nil {
		panic("MCP tool registration requires name, capability, and handler")
	}
	if _, exists := r.tools[name]; exists {
		panic(fmt.Sprintf("MCP tool %q is already registered", name))
	}
	tool.Capability = capability
	r.tools[name] = tool
}

// ListResources returns all registered resource definitions.
func (r *Registry) ListResources() []protocol.ResourceDefinition {
	definitions := make([]protocol.ResourceDefinition, 0, len(r.resources))
	for _, resource := range r.resources {
		definitions = append(definitions, resource.Definition)
	}
	return definitions
}

// ReadResource reads a resource by URI.
func (r *Registry) ReadResource(ctx context.Context, uri string) (*protocol.ResourceReadResult, error) {
	for _, resource := range r.resources {
		if resource.Definition.URI == uri {
			if request, scoped := transport.RequestContextFromContext(ctx); scoped && request.Capability != resource.Capability {
				return nil, &ResourceCapabilityMismatchError{
					URI:                uri,
					DeclaredCapability: request.Capability,
				}
			}
			return r.handleResource(ctx, resource.Definition)
		}
	}
	return nil, fmt.Errorf("resource %q not found", uri)
}

func (r *Registry) registerResource(resource RegisteredResource) {
	uri := strings.TrimSpace(resource.Definition.URI)
	capability := strings.TrimSpace(resource.Capability)
	if uri == "" || capability == "" {
		panic("MCP resource registration requires URI and capability")
	}
	for _, existing := range r.resources {
		if existing.Definition.URI == uri {
			panic(fmt.Sprintf("MCP resource %q is already registered", uri))
		}
	}
	resource.Definition.URI = uri
	resource.Capability = capability
	r.resources = append(r.resources, resource)
}

// ListPrompts returns all registered prompt definitions.
func (r *Registry) ListPrompts() []protocol.PromptDefinition {
	return r.prompts
}

// GetPrompt retrieves and renders a prompt by name with arguments.
func (r *Registry) GetPrompt(name string, args map[string]string) (*protocol.PromptGetResult, error) {
	for _, p := range r.prompts {
		if p.Name == name {
			return r.handlePrompt(p, args)
		}
	}
	return nil, fmt.Errorf("prompt %q not found", name)
}

// ── AgentHub Tool Registration ───────────────────────────────────────

func (r *Registry) registerAgentHubTools() {
	// ── Knowledge Search ─────────────────────────────────────────────
	r.registerTool(RegisteredTool{
		Definition: protocol.ToolDefinition{
			Name:        "knowledge_search",
			Description: "Search the AgentHub knowledge base using semantic (vector) search. Returns relevant document chunks with scores and citations.",
			InputSchema: protocol.ToolInputSchema{
				Type: "object",
				Properties: map[string]protocol.SchemaProperty{
					"query":      {Type: "string", Description: "Natural language search query"},
					"collection": {Type: "string", Description: "Knowledge collection to search", Enum: []any{"docs", "code", "memory", "artifacts"}, Default: "docs"},
					"top_k":      {Type: "integer", Description: "Number of results to return (1-20)", Default: float64(5)},
				},
				Required: []string{"query"},
			},
		},
		Capability: "knowledge.search",
		Handler:    r.knowledgeSearchHandler,
	})

	// ── List Agents ──────────────────────────────────────────────────
	r.registerTool(RegisteredTool{
		Definition: protocol.ToolDefinition{
			Name:        "list_agents",
			Description: "List the authenticated actor's available agents with safe catalog metadata, capabilities, and status.",
			InputSchema: protocol.ToolInputSchema{
				Type:       "object",
				Properties: map[string]protocol.SchemaProperty{},
			},
		},
		Capability: "agent.read",
		Handler:    r.listAgentsHandler,
	})

	// ── List Sessions ────────────────────────────────────────────────
	r.registerTool(RegisteredTool{
		Definition: protocol.ToolDefinition{
			Name:        "list_sessions",
			Description: "List recent chat sessions in the AgentHub platform.",
			InputSchema: protocol.ToolInputSchema{
				Type: "object",
				Properties: map[string]protocol.SchemaProperty{
					"limit": {Type: "integer", Description: "Max sessions to return (1-50)", Default: float64(10)},
				},
			},
		},
		Capability: "session.read",
		Handler:    r.listSessionsHandler,
	})

	// ── Create Workflow ──────────────────────────────────────────────
	r.registerTool(RegisteredTool{
		Definition: protocol.ToolDefinition{
			Name:        "create_workflow",
			Description: "Create a multi-agent workflow (DAG) specifying nodes, edges, and execution strategy.",
			InputSchema: protocol.ToolInputSchema{
				Type: "object",
				Properties: map[string]protocol.SchemaProperty{
					"name":        {Type: "string", Description: "Workflow name"},
					"description": {Type: "string", Description: "Workflow description"},
					"nodes":       {Type: "array", Description: "JSON array of workflow nodes with agent assignments"},
					"edges":       {Type: "array", Description: "JSON array of edges connecting nodes"},
				},
				Required: []string{"name", "nodes"},
			},
		},
		Capability: "workflow.create",
		Handler:    r.createWorkflowHandler,
	})

	// ── Document Ingest ──────────────────────────────────────────────
	r.registerTool(RegisteredTool{
		Definition: protocol.ToolDefinition{
			Name:        "ingest_document",
			Description: "Ingest a document (text/markdown/code) into the knowledge base for later retrieval.",
			InputSchema: protocol.ToolInputSchema{
				Type: "object",
				Properties: map[string]protocol.SchemaProperty{
					"content":    {Type: "string", Description: "The document content to ingest"},
					"title":      {Type: "string", Description: "Document title/name"},
					"collection": {Type: "string", Description: "Target collection", Enum: []any{"docs", "code", "memory", "artifacts"}, Default: "docs"},
					"file_type":  {Type: "string", Description: "File type hint (txt, md, py, go, etc.)", Default: "txt"},
				},
				Required: []string{"content", "title"},
			},
		},
		Capability: "document.ingest",
		Handler:    r.ingestDocumentHandler,
	})

	// ── System Health ────────────────────────────────────────────────
	r.registerTool(RegisteredTool{
		Definition: protocol.ToolDefinition{
			Name:        "system_health",
			Description: "Check the health status of the AgentHub platform and its connected services.",
			InputSchema: protocol.ToolInputSchema{
				Type:       "object",
				Properties: map[string]protocol.SchemaProperty{},
			},
		},
		Capability: "platform.health",
		Handler:    r.systemHealthHandler,
	})
}

// ── Resource Registration ────────────────────────────────────────────

func (r *Registry) registerAgentHubResources() {
	resources := []RegisteredResource{
		{
			Definition: protocol.ResourceDefinition{
				URI:         "agenthub://knowledge/collections",
				Name:        "Knowledge Collections",
				Description: "List of all knowledge base collections with document counts",
				MimeType:    "application/json",
			},
			Capability: "knowledge.read",
		},
		{
			Definition: protocol.ResourceDefinition{
				URI:         "agenthub://agents/manifest",
				Name:        "Agent Manifest",
				Description: "Authenticated actor's available agents with safe catalog metadata",
				MimeType:    "application/json",
			},
			Capability: "agent.read",
		},
		{
			Definition: protocol.ResourceDefinition{
				URI:         "agenthub://templates/catalog",
				Name:        "Template Catalog",
				Description: "All available agent templates from the marketplace",
				MimeType:    "application/json",
			},
			Capability: "template.read",
		},
		{
			Definition: protocol.ResourceDefinition{
				URI:         "agenthub://workspaces/list",
				Name:        "Workspace List",
				Description: "List of workspaces with member counts",
				MimeType:    "application/json",
			},
			Capability: "workspace.read",
		},
	}
	for _, resource := range resources {
		r.registerResource(resource)
	}
}

// ── Prompt Registration ──────────────────────────────────────────────

func (r *Registry) registerAgentHubPrompts() {
	r.prompts = []protocol.PromptDefinition{
		{
			Name:        "agent_architect",
			Description: "Prompt template for the Architect (PM/PMO) agent — task decomposition, scheduling, and arbitration.",
			Arguments: []protocol.PromptArgument{
				{Name: "task", Description: "The task description to decompose", Required: true},
				{Name: "context", Description: "Additional context or constraints", Required: false},
			},
		},
		{
			Name:        "code_review",
			Description: "Prompt template for code review — security, performance, and style analysis.",
			Arguments: []protocol.PromptArgument{
				{Name: "code", Description: "The code to review", Required: true},
				{Name: "language", Description: "Programming language", Required: false},
			},
		},
		{
			Name:        "knowledge_qa",
			Description: "Prompt template for RAG-based Q&A with citation requirements.",
			Arguments: []protocol.PromptArgument{
				{Name: "question", Description: "The question to answer", Required: true},
				{Name: "context", Description: "Retrieved context (auto-filled)", Required: false},
			},
		},
	}
}

// ── Tool Handlers ────────────────────────────────────────────────────

func (r *Registry) knowledgeSearchHandler(ctx context.Context, args map[string]any) (*protocol.ToolCallResult, error) {
	query := getStringArg(args, "query")
	if query == "" {
		return errorResult("query is required"), nil
	}
	collection := getStringArg(args, "collection")
	if collection == "" {
		collection = "docs"
	}
	topK := getIntArg(args, "top_k", 5)

	body, _ := json.Marshal(map[string]any{
		"query":      query,
		"collection": collection,
		"k":          topK,
	})

	req, _ := http.NewRequestWithContext(ctx, "POST", r.knowledgeURL+"/retrieval-test", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return errorResult(fmt.Sprintf("Knowledge search failed: %v", err)), nil
	}
	defer resp.Body.Close()

	var result map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return errorResult(fmt.Sprintf("Failed to parse knowledge response: %v", err)), nil
	}

	resultsJSON, _ := json.MarshalIndent(result, "", "  ")
	return &protocol.ToolCallResult{
		Content: []protocol.ToolContent{
			{Type: "text", Text: string(resultsJSON)},
		},
	}, nil
}

func (r *Registry) listAgentsHandler(ctx context.Context, _ map[string]any) (*protocol.ToolCallResult, error) {
	data, err := r.fetchAgentCatalog(ctx)
	if err != nil {
		if errors.Is(err, ErrTenantIdentityRequired) || errors.Is(err, ErrDownstreamCredentialRequired) {
			return nil, err
		}
		return errorResult(fmt.Sprintf("Failed to list agents: %v", err)), nil
	}
	return &protocol.ToolCallResult{
		Content: []protocol.ToolContent{
			{Type: "text", Text: string(data)},
		},
	}, nil
}

func (r *Registry) fetchAgentCatalog(ctx context.Context) ([]byte, error) {
	principal, err := tenantPrincipal(ctx)
	if err != nil {
		return nil, err
	}
	authorization, ok := mcpauth.AuthorizationHeaderFromContext(ctx)
	if !ok {
		return nil, ErrDownstreamCredentialRequired
	}

	endpoint, err := url.Parse(strings.TrimRight(r.gatewayURL, "/") + "/platform/agent-registry")
	if err != nil {
		return nil, fmt.Errorf("build agent registry endpoint: %w", err)
	}
	query := endpoint.Query()
	query.Set("tenant_id", principal.TenantID)
	endpoint.RawQuery = query.Encode()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return nil, fmt.Errorf("build agent registry request: %w", err)
	}
	req.Header.Set("Authorization", authorization)
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request agent registry: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		return nil, fmt.Errorf("gateway returned HTTP %d", resp.StatusCode)
	}

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read agent registry response: %w", err)
	}
	if !json.Valid(data) {
		return nil, errors.New("gateway returned invalid JSON")
	}
	return data, nil
}

func (r *Registry) listSessionsHandler(ctx context.Context, args map[string]any) (*protocol.ToolCallResult, error) {
	principal, err := tenantPrincipal(ctx)
	if err != nil {
		return nil, err
	}
	authorization, ok := mcpauth.AuthorizationHeaderFromContext(ctx)
	if !ok {
		return nil, ErrDownstreamCredentialRequired
	}
	limit := normalizedLimit(getIntArg(args, "limit", 10), 10, 50)

	endpoint, err := url.Parse(strings.TrimRight(r.gatewayURL, "/") + "/platform/sessions")
	if err != nil {
		return errorResult(fmt.Sprintf("Failed to build session request: %v", err)), nil
	}
	query := endpoint.Query()
	query.Set("tenant_id", principal.TenantID)
	query.Set("limit", strconv.Itoa(limit))
	endpoint.RawQuery = query.Encode()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return errorResult(fmt.Sprintf("Failed to build session request: %v", err)), nil
	}
	req.Header.Set("Authorization", authorization)
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return errorResult(fmt.Sprintf("Failed to list sessions: %v", err)), nil
	}
	defer resp.Body.Close()
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		return errorResult(fmt.Sprintf("Failed to list sessions: gateway returned HTTP %d", resp.StatusCode)), nil
	}

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return errorResult(fmt.Sprintf("Failed to read session response: %v", err)), nil
	}
	if !json.Valid(data) {
		return errorResult("Failed to list sessions: gateway returned invalid JSON"), nil
	}
	return &protocol.ToolCallResult{
		Content: []protocol.ToolContent{
			{Type: "text", Text: string(data)},
		},
	}, nil
}

func (r *Registry) createWorkflowHandler(ctx context.Context, args map[string]any) (*protocol.ToolCallResult, error) {
	name := getStringArg(args, "name")
	if name == "" {
		return errorResult("name is required"), nil
	}

	body, _ := json.Marshal(args)
	req, _ := http.NewRequestWithContext(ctx, "POST", r.gatewayURL+"/platform/workflows", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return errorResult(fmt.Sprintf("Workflow creation failed: %v", err)), nil
	}
	defer resp.Body.Close()

	data, _ := io.ReadAll(resp.Body)
	return &protocol.ToolCallResult{
		Content: []protocol.ToolContent{
			{Type: "text", Text: string(data)},
		},
	}, nil
}

func (r *Registry) ingestDocumentHandler(ctx context.Context, args map[string]any) (*protocol.ToolCallResult, error) {
	content := getStringArg(args, "content")
	title := getStringArg(args, "title")
	if content == "" || title == "" {
		return errorResult("content and title are required"), nil
	}
	collection := getStringArg(args, "collection")
	if collection == "" {
		collection = "docs"
	}
	fileType := getStringArg(args, "file_type")
	if fileType == "" {
		fileType = "txt"
	}
	principal, err := tenantPrincipal(ctx)
	if err != nil {
		return nil, err
	}

	body, _ := json.Marshal(map[string]any{
		"request_id":   fmt.Sprintf("mcp-ingest-%d", time.Now().UnixNano()),
		"tenant_id":    principal.TenantID,
		"source_id":    title,
		"collection":   collection,
		"content_type": "text/plain",
		"content":      content,
		"metadata": map[string]any{
			"actor_id":  principal.UserID,
			"file_type": fileType,
			"source":    "mcp-gateway",
		},
	})

	req, _ := http.NewRequestWithContext(ctx, "POST", r.knowledgeURL+"/ingest", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return errorResult(fmt.Sprintf("Document ingest failed: %v", err)), nil
	}
	defer resp.Body.Close()

	data, _ := io.ReadAll(resp.Body)
	return &protocol.ToolCallResult{
		Content: []protocol.ToolContent{
			{Type: "text", Text: string(data)},
		},
	}, nil
}

func tenantPrincipal(ctx context.Context) (iam.TenantContext, error) {
	principal, ok := iam.FromContext(ctx)
	if !ok || strings.TrimSpace(principal.TenantID) == "" || strings.TrimSpace(principal.UserID) == "" {
		return iam.TenantContext{}, ErrTenantIdentityRequired
	}
	return principal, nil
}

func (r *Registry) systemHealthHandler(ctx context.Context, _ map[string]any) (*protocol.ToolCallResult, error) {
	services := map[string]string{}
	client := &http.Client{Timeout: 3 * time.Second}

	checks := map[string]string{
		"gateway":   r.gatewayURL + "/healthz",
		"knowledge": r.knowledgeURL + "/healthz",
	}

	for name, url := range checks {
		resp, err := client.Get(url)
		if err != nil || resp.StatusCode != 200 {
			services[name] = "unhealthy"
		} else {
			services[name] = "healthy"
			resp.Body.Close()
		}
	}

	// Also check MCP gateway's own health
	services["mcp-gateway"] = "healthy"

	result, _ := json.MarshalIndent(map[string]any{
		"status":   "ok",
		"services": services,
		"version":  "1.0.0",
	}, "", "  ")

	return &protocol.ToolCallResult{
		Content: []protocol.ToolContent{
			{Type: "text", Text: string(result)},
		},
	}, nil
}

// ── Resource Handlers ────────────────────────────────────────────────

func (r *Registry) handleResource(ctx context.Context, res protocol.ResourceDefinition) (*protocol.ResourceReadResult, error) {
	if res.URI == "agenthub://agents/manifest" {
		data, err := r.fetchAgentCatalog(ctx)
		if err != nil {
			return nil, fmt.Errorf("agent manifest fetch failed: %w", err)
		}
		return resourceTextResult(res, data), nil
	}

	var endpoint string
	switch res.URI {
	case "agenthub://knowledge/collections":
		endpoint = r.knowledgeURL + "/collections"
	case "agenthub://templates/catalog":
		endpoint = r.gatewayURL + "/platform/templates"
	case "agenthub://workspaces/list":
		endpoint = r.gatewayURL + "/platform/workspaces"
	default:
		return nil, fmt.Errorf("unknown resource URI: %s", res.URI)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, fmt.Errorf("build resource request: %w", err)
	}
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("resource fetch failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		return nil, fmt.Errorf("resource endpoint returned HTTP %d", resp.StatusCode)
	}

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read resource response: %w", err)
	}
	if strings.EqualFold(res.MimeType, "application/json") && !json.Valid(data) {
		return nil, errors.New("resource endpoint returned invalid JSON")
	}
	return resourceTextResult(res, data), nil
}

func resourceTextResult(res protocol.ResourceDefinition, data []byte) *protocol.ResourceReadResult {
	return &protocol.ResourceReadResult{
		Contents: []protocol.ResourceContent{
			{URI: res.URI, MimeType: res.MimeType, Text: string(data)},
		},
	}
}

// ── Prompt Handlers ──────────────────────────────────────────────────

func (r *Registry) handlePrompt(p protocol.PromptDefinition, args map[string]string) (*protocol.PromptGetResult, error) {
	switch p.Name {
	case "agent_architect":
		task := args["task"]
		ctx := args["context"]
		prompt := fmt.Sprintf(
			"You are the Architect, the primary PM/PMO agent of AgentHub.\n\n"+
				"## Task\n%s\n\n"+
				"## Responsibilities\n"+
				"1. **Decompose** the task into a DAG of subtasks\n"+
				"2. **Assign** each subtask to the most suitable agent\n"+
				"3. **Schedule** parallel and sequential execution\n"+
				"4. **Monitor** progress and handle failures\n"+
				"5. **Arbitrate** conflicts between agent outputs\n\n"+
				"## Output Format\n"+
				"Return a structured plan with: task breakdown, agent assignments, execution order, and success criteria.",
			task,
		)
		if ctx != "" {
			prompt += fmt.Sprintf("\n\n## Additional Context\n%s", ctx)
		}
		return &protocol.PromptGetResult{
			Description: p.Description,
			Messages: []protocol.PromptMessage{
				{Role: "user", Content: protocol.PromptContent{Type: "text", Text: prompt}},
			},
		}, nil

	case "code_review":
		code := args["code"]
		lang := args["language"]
		if lang == "" {
			lang = "code"
		}
		prompt := fmt.Sprintf(
			"Please review the following %s code. Analyze:\n"+
				"1. **Security** — vulnerabilities, injection risks, unsafe patterns\n"+
				"2. **Performance** — bottlenecks, memory leaks, inefficient algorithms\n"+
				"3. **Style** — readability, naming, consistency with idioms\n"+
				"4. **Correctness** — logic errors, edge cases, error handling\n\n"+
				"```%s\n%s\n```\n\n"+
				"Provide specific, actionable feedback with line references.",
			lang, lang, code,
		)
		return &protocol.PromptGetResult{
			Description: p.Description,
			Messages: []protocol.PromptMessage{
				{Role: "user", Content: protocol.PromptContent{Type: "text", Text: prompt}},
			},
		}, nil

	case "knowledge_qa":
		question := args["question"]
		ctx := args["context"]
		prompt := fmt.Sprintf(
			"Answer the following question based on the provided context.\n"+
				"Always cite specific sources using the format [source: chunk-N].\n\n"+
				"## Question\n%s\n\n"+
				"## Context\n%s\n\n"+
				"## Requirements\n"+
				"1. Answer concisely and accurately\n"+
				"2. Include citations for every factual claim\n"+
				"3. If the context doesn't contain sufficient information, say so explicitly\n"+
				"4. Use code blocks for any code references",
			question, ctx,
		)
		return &protocol.PromptGetResult{
			Description: p.Description,
			Messages: []protocol.PromptMessage{
				{Role: "user", Content: protocol.PromptContent{Type: "text", Text: prompt}},
			},
		}, nil

	default:
		return nil, fmt.Errorf("unknown prompt: %s", p.Name)
	}
}

// ── Helpers ──────────────────────────────────────────────────────────

func getStringArg(args map[string]any, key string) string {
	if v, ok := args[key]; ok {
		if s, ok := v.(string); ok {
			return strings.TrimSpace(s)
		}
	}
	return ""
}

func getIntArg(args map[string]any, key string, defaultVal int) int {
	if v, ok := args[key]; ok {
		switch n := v.(type) {
		case float64:
			return int(n)
		case int:
			return n
		case int64:
			return int(n)
		}
	}
	return defaultVal
}

func normalizedLimit(value, defaultValue, maxValue int) int {
	if value < 1 {
		return defaultValue
	}
	if value > maxValue {
		return maxValue
	}
	return value
}

func errorResult(msg string) *protocol.ToolCallResult {
	return &protocol.ToolCallResult{
		Content: []protocol.ToolContent{
			{Type: "text", Text: fmt.Sprintf("Error: %s", msg)},
		},
		IsError: true,
	}
}

// ── Logging ──────────────────────────────────────────────────────────

var Logger = log.New(os.Stderr, "[mcp-gateway] ", log.LstdFlags|log.Lmsgprefix)
