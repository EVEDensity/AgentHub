package transport

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
)

const defaultStatelessBodyLimit int64 = 1 << 20

// MCPRequestContext is the per-call identity and capability context required
// by the stateless HTTP transport. It is request metadata, not MCP session
// state or Mission lifecycle state.
type MCPRequestContext struct {
	MissionID  string
	WorkUnitID string
	Attempt    int
	Capability string
	Scope      map[string]any
	TraceID    string
}

type requestContextKey struct{}

// RequestContextFromContext returns the validated context attached by the
// stateless transport.
func RequestContextFromContext(ctx context.Context) (MCPRequestContext, bool) {
	value, ok := ctx.Value(requestContextKey{}).(MCPRequestContext)
	return value, ok
}

// StatelessHTTPTransport handles one JSON-RPC request per HTTP POST. It does
// not create or look up sessions and can be safely mounted on every instance.
type StatelessHTTPTransport struct {
	handler      MessageHandler
	maxBodyBytes int64
	authorize    StatelessAuthorizer
}

// StatelessAuthorizer is the server-side authorization boundary for one
// stateless MCP request. Authentication is expected to have run upstream and
// placed the principal in the context; the authorizer must not trust the
// X-AgentHub-* headers as identity proof.
type StatelessAuthorizer func(context.Context, MCPRequestContext) error

// AuthorizationError lets an authorizer distinguish authentication failures
// (401) from authenticated-but-forbidden requests (403) without coupling the
// transport package to a particular IAM implementation.
type AuthorizationError struct {
	Status  int
	Message string
}

func (e *AuthorizationError) Error() string {
	if e.Message == "" {
		return "MCP request is not authorized"
	}
	return e.Message
}

// NewStatelessHTTPTransport creates a stateless transport with a bounded body.
func NewStatelessHTTPTransport(handler MessageHandler, maxBodyBytes int64) *StatelessHTTPTransport {
	return NewStatelessHTTPTransportWithAuthorizer(handler, maxBodyBytes, nil)
}

// NewStatelessHTTPTransportWithAuthorizer creates a stateless transport with
// an injectable identity/capability authorization boundary.
func NewStatelessHTTPTransportWithAuthorizer(
	handler MessageHandler,
	maxBodyBytes int64,
	authorize StatelessAuthorizer,
) *StatelessHTTPTransport {
	if maxBodyBytes <= 0 {
		maxBodyBytes = defaultStatelessBodyLimit
	}
	return &StatelessHTTPTransport{
		handler:      handler,
		maxBodyBytes: maxBodyBytes,
		authorize:    authorize,
	}
}

// NewStatelessHTTPHandler returns a handler using the default body limit.
func NewStatelessHTTPHandler(handler MessageHandler) http.Handler {
	return NewStatelessHTTPTransport(handler, defaultStatelessBodyLimit)
}

// NewStatelessHTTPHandlerWithAuthorizer returns a stateless handler with an
// authorization callback evaluated after request context validation.
func NewStatelessHTTPHandlerWithAuthorizer(
	handler MessageHandler,
	authorize StatelessAuthorizer,
) http.Handler {
	return NewStatelessHTTPTransportWithAuthorizer(
		handler,
		defaultStatelessBodyLimit,
		authorize,
	)
}

// ServeHTTP validates request context and dispatches exactly one JSON-RPC
// message without requiring an initialize handshake or session ID.
func (t *StatelessHTTPTransport) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		http.Error(w, "stateless MCP endpoint requires POST", http.StatusMethodNotAllowed)
		return
	}

	requestContext, err := parseRequestContext(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, t.maxBodyBytes))
	if err != nil {
		http.Error(w, "MCP request body is too large or unreadable", http.StatusRequestEntityTooLarge)
		return
	}
	if len(body) == 0 || !json.Valid(body) {
		http.Error(w, "MCP request body must be valid JSON", http.StatusBadRequest)
		return
	}

	ctx := context.WithValue(r.Context(), requestContextKey{}, requestContext)
	if t.authorize != nil {
		if err := t.authorize(ctx, requestContext); err != nil {
			status := http.StatusForbidden
			var authErr *AuthorizationError
			if errors.As(err, &authErr) &&
				(authErr.Status == http.StatusUnauthorized || authErr.Status == http.StatusForbidden) {
				status = authErr.Status
			}
			http.Error(w, err.Error(), status)
			return
		}
	}
	responses, err := t.handler(ctx, json.RawMessage(body))
	if err != nil {
		http.Error(w, "MCP request dispatch failed", http.StatusInternalServerError)
		return
	}
	if len(responses) == 0 {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if len(responses) != 1 {
		http.Error(w, "MCP stateless endpoint does not support response batches", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(responses[0])
}

func parseRequestContext(r *http.Request) (MCPRequestContext, error) {
	missionID, err := requiredHeader(r, "X-AgentHub-Mission-Id")
	if err != nil {
		return MCPRequestContext{}, err
	}
	workUnitID, err := requiredHeader(r, "X-AgentHub-Work-Unit-Id")
	if err != nil {
		return MCPRequestContext{}, err
	}
	capability, err := requiredHeader(r, "X-AgentHub-Capability")
	if err != nil {
		return MCPRequestContext{}, err
	}
	attemptValue, err := requiredHeader(r, "X-AgentHub-Attempt")
	if err != nil {
		return MCPRequestContext{}, err
	}
	attempt, err := strconv.Atoi(attemptValue)
	if err != nil || attempt < 1 {
		return MCPRequestContext{}, fmt.Errorf("X-AgentHub-Attempt must be a positive integer")
	}

	scopeValue, err := requiredHeader(r, "X-AgentHub-Capability-Scope")
	if err != nil {
		return MCPRequestContext{}, err
	}
	var scope map[string]any
	if err := json.Unmarshal([]byte(scopeValue), &scope); err != nil || scope == nil {
		return MCPRequestContext{}, fmt.Errorf("X-AgentHub-Capability-Scope must be a JSON object")
	}

	return MCPRequestContext{
		MissionID:  missionID,
		WorkUnitID: workUnitID,
		Attempt:    attempt,
		Capability: capability,
		Scope:      scope,
		TraceID:    strings.TrimSpace(r.Header.Get("X-AgentHub-Trace-Id")),
	}, nil
}

func requiredHeader(r *http.Request, name string) (string, error) {
	value := strings.TrimSpace(r.Header.Get(name))
	if value == "" {
		return "", fmt.Errorf("%s header is required", name)
	}
	return value, nil
}
