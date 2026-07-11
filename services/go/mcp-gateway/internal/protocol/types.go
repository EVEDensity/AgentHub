// Package protocol implements the Model Context Protocol (MCP) over JSON-RPC 2.0.
//
// MCP spec: https://spec.modelcontextprotocol.io/
// Transport: STDIO (stdin/stdout) + SSE (Server-Sent Events over HTTP)
//
// This package defines all JSON-RPC 2.0 envelope types and MCP-specific
// request/response/notification message types used by the gateway.
package protocol

import "encoding/json"

// ── JSON-RPC 2.0 Envelope ────────────────────────────────────────────

// Request represents a JSON-RPC 2.0 request or notification.
// Notifications omit the ID field (null in JSON).
type Request struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      *int64         `json:"id,omitempty"` // nil → notification
	Method  string         `json:"method"`
	Params  map[string]any `json:"params,omitempty"`
}

// Response represents a JSON-RPC 2.0 success response.
type Response struct {
	JSONRPC string `json:"jsonrpc"`
	ID      int64  `json:"id"`
	Result  any    `json:"result"`
}

// ErrorResponse represents a JSON-RPC 2.0 error response.
type ErrorResponse struct {
	JSONRPC string    `json:"jsonrpc"`
	ID      *int64    `json:"id"` // null if request ID was unknown
	Error   RPCError  `json:"error"`
}

// RPCError is the JSON-RPC 2.0 error object.
type RPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

// ── JSON-RPC 2.0 Standard Error Codes ─────────────────────────────────

const (
	ErrParseError     = -32700
	ErrInvalidRequest = -32600
	ErrMethodNotFound = -32601
	ErrInvalidParams  = -32602
	ErrInternalError  = -32603
)

// ── MCP Initialize ───────────────────────────────────────────────────

// ServerCapabilities describes what this MCP server can do.
type ServerCapabilities struct {
	Tools     *ToolsCapability     `json:"tools,omitempty"`
	Resources *ResourcesCapability `json:"resources,omitempty"`
	Prompts   *PromptsCapability   `json:"prompts,omitempty"`
	Logging   *LoggingCapability   `json:"logging,omitempty"`
}

type ToolsCapability struct {
	ListChanged bool `json:"listChanged,omitempty"`
}

type ResourcesCapability struct {
	Subscribe   bool `json:"subscribe,omitempty"`
	ListChanged bool `json:"listChanged,omitempty"`
}

type PromptsCapability struct {
	ListChanged bool `json:"listChanged,omitempty"`
}

type LoggingCapability struct{}

// ServerInfo identifies this MCP server implementation.
type ServerInfo struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}

// InitializeResult is the response to the initialize request.
type InitializeResult struct {
	ProtocolVersion string             `json:"protocolVersion"`
	Capabilities    ServerCapabilities `json:"capabilities"`
	ServerInfo      ServerInfo         `json:"serverInfo"`
	Instructions    string             `json:"instructions,omitempty"`
}

// ── MCP Tools ────────────────────────────────────────────────────────

// ToolDefinition describes a tool available via MCP.
type ToolDefinition struct {
	Name        string            `json:"name"`
	Description string            `json:"description,omitempty"`
	InputSchema ToolInputSchema   `json:"inputSchema"`
}

// ToolInputSchema is a JSON Schema for the tool's input parameters.
type ToolInputSchema struct {
	Type       string                    `json:"type"`
	Properties map[string]SchemaProperty `json:"properties,omitempty"`
	Required   []string                  `json:"required,omitempty"`
}

// SchemaProperty describes a single property in a JSON Schema.
type SchemaProperty struct {
	Type        string `json:"type"`
	Description string `json:"description,omitempty"`
	Enum        []any  `json:"enum,omitempty"`
	Default     any    `json:"default,omitempty"`
}

// ToolCallParams are the params for tools/call.
type ToolCallParams struct {
	Name      string         `json:"name"`
	Arguments map[string]any `json:"arguments,omitempty"`
}

// ToolCallResult is the result returned by a tool execution.
type ToolCallResult struct {
	Content []ToolContent `json:"content"`
	IsError bool          `json:"isError,omitempty"`
}

// ToolContent represents a single content block in a tool result.
type ToolContent struct {
	Type     string `json:"type"` // "text" | "image" | "resource"
	Text     string `json:"text,omitempty"`
	Data     string `json:"data,omitempty"`     // base64 for images
	MimeType string `json:"mimeType,omitempty"` // for images/resources
}

// ── MCP Resources ────────────────────────────────────────────────────

// ResourceDefinition describes a resource exposed via MCP.
type ResourceDefinition struct {
	URI         string `json:"uri"`
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	MimeType    string `json:"mimeType,omitempty"`
}

// ResourceReadParams are the params for resources/read.
type ResourceReadParams struct {
	URI string `json:"uri"`
}

// ResourceReadResult is the result of reading a resource.
type ResourceReadResult struct {
	Contents []ResourceContent `json:"contents"`
}

// ResourceContent represents a single resource content item.
type ResourceContent struct {
	URI      string `json:"uri"`
	MimeType string `json:"mimeType,omitempty"`
	Text     string `json:"text,omitempty"`
	Blob     string `json:"blob,omitempty"` // base64
}

// ── MCP Prompts ──────────────────────────────────────────────────────

// PromptDefinition describes a prompt template.
type PromptDefinition struct {
	Name        string               `json:"name"`
	Description string               `json:"description,omitempty"`
	Arguments   []PromptArgument     `json:"arguments,omitempty"`
}

// PromptArgument describes an argument a prompt can accept.
type PromptArgument struct {
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	Required    bool   `json:"required,omitempty"`
}

// PromptGetParams are the params for prompts/get.
type PromptGetParams struct {
	Name      string            `json:"name"`
	Arguments map[string]string `json:"arguments,omitempty"`
}

// PromptGetResult is the result of getting a prompt.
type PromptGetResult struct {
	Description string          `json:"description,omitempty"`
	Messages    []PromptMessage `json:"messages"`
}

// PromptMessage is a single message in a prompt result.
type PromptMessage struct {
	Role    string        `json:"role"` // "user" | "assistant"
	Content PromptContent `json:"content"`
}

// PromptContent is the content of a prompt message.
type PromptContent struct {
	Type     string `json:"type"` // "text" | "image" | "resource"
	Text     string `json:"text,omitempty"`
	Data     string `json:"data,omitempty"`
	MimeType string `json:"mimeType,omitempty"`
}

// ── MCP Logging ──────────────────────────────────────────────────────

// LogMessage is a logging notification sent from server to client.
type LogMessage struct {
	Level  string `json:"level"` // "debug" | "info" | "warning" | "error"
	Logger string `json:"logger,omitempty"`
	Data   any    `json:"data"`
}

// ── Helpers ──────────────────────────────────────────────────────────

// NewResult creates a success Response for a given request ID.
func NewResult(id int64, result any) Response {
	return Response{JSONRPC: "2.0", ID: id, Result: result}
}

// NewError creates an ErrorResponse for a given request ID.
func NewError(id *int64, code int, message string, data any) ErrorResponse {
	return ErrorResponse{
		JSONRPC: "2.0",
		ID:      id,
		Error:   RPCError{Code: code, Message: message, Data: data},
	}
}

// NewNotification creates a notification Request (no ID).
func NewNotification(method string, params map[string]any) Request {
	return Request{JSONRPC: "2.0", Method: method, Params: params}
}

// MarshalResponse marshals a response/error to JSON bytes with a trailing newline.
func MarshalResponse(v any) ([]byte, error) {
	b, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	return append(b, '\n'), nil
}
