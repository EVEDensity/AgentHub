package protocol

import (
	"context"
	"encoding/json"
	"fmt"
	"sync/atomic"
)

// Handler dispatches incoming JSON-RPC requests to the appropriate MCP method.
// It implements the full MCP server-side lifecycle: initialize → ready → serve.
type Handler struct {
	info         ServerInfo
	capabilities ServerCapabilities
	registry     ToolRegistry // interface to avoid circular imports
	initialized  atomic.Bool
	nextID       atomic.Int64
}

// ToolRegistry is the interface that the handler needs for tool/resource/prompt operations.
// Implemented by registry.Registry in the registry package.
type ToolRegistry interface {
	ListTools() []ToolDefinition
	CallTool(ctx context.Context, name string, args map[string]any) (*ToolCallResult, error)
	ListResources() []ResourceDefinition
	ReadResource(ctx context.Context, uri string) (*ResourceReadResult, error)
	ListPrompts() []PromptDefinition
	GetPrompt(name string, args map[string]string) (*PromptGetResult, error)
}

// NewHandler creates a new MCP protocol handler.
func NewHandler(info ServerInfo, caps ServerCapabilities, registry ToolRegistry) *Handler {
	return &Handler{
		info:         info,
		capabilities: caps,
		registry:     registry,
	}
}

// IsInitialized returns whether the client has completed the initialize handshake.
func (h *Handler) IsInitialized() bool {
	return h.initialized.Load()
}

// Dispatch handles a single JSON-RPC request and returns the appropriate response(s).
// Returns nil, nil for notifications that produce no direct response.
func (h *Handler) Dispatch(ctx context.Context, raw json.RawMessage) ([]any, error) {
	// Determine if this is a request or notification
	var req Request
	if err := json.Unmarshal(raw, &req); err != nil {
		errResp := NewError(nil, ErrParseError, "Parse error: "+err.Error(), nil)
		return []any{errResp}, nil
	}

	// Ensure jsonrpc field is present
	if req.JSONRPC != "2.0" {
		errResp := NewError(req.ID, ErrInvalidRequest, "Invalid Request: jsonrpc must be '2.0'", nil)
		return []any{errResp}, nil
	}

	// Route to the appropriate handler
	switch req.Method {
	// ── Lifecycle ──────────────────────────────────────────────────
	case "initialize":
		return h.handleInitialize(req)
	case "initialized":
		return h.handleInitialized(req)
	case "ping":
		return h.handlePing(req)

	// ── Tools ──────────────────────────────────────────────────────
	case "tools/list":
		return h.handleToolsList(req)
	case "tools/call":
		return h.handleToolsCall(ctx, req)

	// ── Resources ──────────────────────────────────────────────────
	case "resources/list":
		return h.handleResourcesList(req)
	case "resources/read":
		return h.handleResourcesRead(ctx, req)

	// ── Prompts ────────────────────────────────────────────────────
	case "prompts/list":
		return h.handlePromptsList(req)
	case "prompts/get":
		return h.handlePromptsGet(req)

	// ── Unknown ────────────────────────────────────────────────────
	default:
		errResp := NewError(req.ID, ErrMethodNotFound,
			fmt.Sprintf("Method not found: %s", req.Method), nil)
		return []any{errResp}, nil
	}
}

// ── Lifecycle Handlers ───────────────────────────────────────────────

func (h *Handler) handleInitialize(req Request) ([]any, error) {
	if req.ID == nil {
		return nil, fmt.Errorf("initialize requires an ID")
	}

	// Extract client info from params (optional)
	_ = req.Params // client info, protocol version, capabilities — accepted but not validated strictly

	result := InitializeResult{
		ProtocolVersion: "2024-11-05",
		Capabilities:    h.capabilities,
		ServerInfo:      h.info,
		Instructions:    "AgentHub MCP Gateway — use tools/list to discover available tools, resources/list for knowledge access, and prompts/list for prompt templates.",
	}

	return []any{NewResult(*req.ID, result)}, nil
}

func (h *Handler) handleInitialized(req Request) ([]any, error) {
	h.initialized.Store(true)
	// This is a notification — no response needed.
	return nil, nil
}

func (h *Handler) handlePing(req Request) ([]any, error) {
	if req.ID == nil {
		return nil, nil // notification ping — no response
	}
	return []any{NewResult(*req.ID, map[string]any{})}, nil
}

// ── Tool Handlers ────────────────────────────────────────────────────

func (h *Handler) handleToolsList(req Request) ([]any, error) {
	if req.ID == nil {
		return nil, nil
	}
	tools := h.registry.ListTools()
	return []any{NewResult(*req.ID, map[string]any{"tools": tools})}, nil
}

func (h *Handler) handleToolsCall(ctx context.Context, req Request) ([]any, error) {
	if req.ID == nil {
		return nil, nil
	}

	// Extract tool call params
	var params ToolCallParams
	if req.Params != nil {
		if name, ok := req.Params["name"].(string); ok {
			params.Name = name
		}
		if args, ok := req.Params["arguments"].(map[string]any); ok {
			params.Arguments = args
		}
	}

	if params.Name == "" {
		errResp := NewError(req.ID, ErrInvalidParams, "tool name is required", nil)
		return []any{errResp}, nil
	}

	result, err := h.registry.CallTool(ctx, params.Name, params.Arguments)
	if err != nil {
		errResp := NewError(req.ID, ErrInternalError, err.Error(), nil)
		return []any{errResp}, nil
	}

	return []any{NewResult(*req.ID, result)}, nil
}

// ── Resource Handlers ────────────────────────────────────────────────

func (h *Handler) handleResourcesList(req Request) ([]any, error) {
	if req.ID == nil {
		return nil, nil
	}
	resources := h.registry.ListResources()
	return []any{NewResult(*req.ID, map[string]any{"resources": resources})}, nil
}

func (h *Handler) handleResourcesRead(ctx context.Context, req Request) ([]any, error) {
	if req.ID == nil {
		return nil, nil
	}

	var params ResourceReadParams
	if req.Params != nil {
		if uri, ok := req.Params["uri"].(string); ok {
			params.URI = uri
		}
	}

	if params.URI == "" {
		errResp := NewError(req.ID, ErrInvalidParams, "resource uri is required", nil)
		return []any{errResp}, nil
	}

	result, err := h.registry.ReadResource(ctx, params.URI)
	if err != nil {
		errResp := NewError(req.ID, ErrInternalError, err.Error(), nil)
		return []any{errResp}, nil
	}

	return []any{NewResult(*req.ID, result)}, nil
}

// ── Prompt Handlers ──────────────────────────────────────────────────

func (h *Handler) handlePromptsList(req Request) ([]any, error) {
	if req.ID == nil {
		return nil, nil
	}
	prompts := h.registry.ListPrompts()
	return []any{NewResult(*req.ID, map[string]any{"prompts": prompts})}, nil
}

func (h *Handler) handlePromptsGet(req Request) ([]any, error) {
	if req.ID == nil {
		return nil, nil
	}

	var params PromptGetParams
	if req.Params != nil {
		if name, ok := req.Params["name"].(string); ok {
			params.Name = name
		}
		if args, ok := req.Params["arguments"].(map[string]any); ok {
			params.Arguments = make(map[string]string)
			for k, v := range args {
				if s, ok := v.(string); ok {
					params.Arguments[k] = s
				}
			}
		}
	}

	if params.Name == "" {
		errResp := NewError(req.ID, ErrInvalidParams, "prompt name is required", nil)
		return []any{errResp}, nil
	}

	result, err := h.registry.GetPrompt(params.Name, params.Arguments)
	if err != nil {
		errResp := NewError(req.ID, ErrInternalError, err.Error(), nil)
		return []any{errResp}, nil
	}

	return []any{NewResult(*req.ID, result)}, nil
}
