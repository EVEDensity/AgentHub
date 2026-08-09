package registry

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	mcpauth "github.com/agenthub/mcp-gateway/internal/auth"
	"github.com/agenthub/mcp-gateway/internal/protocol"
	"github.com/agenthub/mcp-gateway/internal/transport"
	"github.com/agenthub/platform/shared/iam"
)

func testRegistry(t *testing.T, called *bool) *Registry {
	t.Helper()
	r := &Registry{tools: make(map[string]RegisteredTool)}
	r.registerTool(RegisteredTool{
		Definition: protocol.ToolDefinition{
			Name:        "read_file",
			InputSchema: protocol.ToolInputSchema{Type: "object"},
		},
		Capability: "repository.read",
		Handler: func(context.Context, map[string]any) (*protocol.ToolCallResult, error) {
			*called = true
			return &protocol.ToolCallResult{}, nil
		},
	})
	return r
}

func executionContext(capability string) context.Context {
	return transport.WithRequestContext(context.Background(), transport.MCPRequestContext{
		MissionID:  "mission-1",
		WorkUnitID: "work-unit-1",
		Attempt:    1,
		Capability: capability,
		Scope:      map[string]any{},
	})
}

func authenticatedIdentityContext(t *testing.T) (context.Context, string) {
	t.Helper()
	issuer := iam.NewTokenIssuer([]byte("test-secret-32bytes-xxxxxxxxxxxx"), "iam-service", time.Hour)
	token, err := issuer.Issue(iam.Claims{
		TenantID: "tenant-real",
		UserID:   "actor-real",
		Scopes:   []string{iam.ScopeToolExecute},
	})
	if err != nil {
		t.Fatalf("issue token: %v", err)
	}
	var ctx context.Context
	handler := mcpauth.Middleware(issuer, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx = r.Context()
		w.WriteHeader(http.StatusNoContent)
	}), nil)
	req := httptest.NewRequest(http.MethodPost, "/mcp/rpc", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	handler.ServeHTTP(httptest.NewRecorder(), req)
	if ctx == nil {
		t.Fatal("authenticated context was not captured")
	}
	return ctx, "Bearer " + token
}

func TestRegistryAllowsToolBoundToDeclaredCapability(t *testing.T) {
	called := false
	r := testRegistry(t, &called)
	if _, err := r.CallTool(executionContext("repository.read"), "read_file", nil); err != nil {
		t.Fatalf("call bound tool: %v", err)
	}
	if !called {
		t.Fatal("bound tool handler was not called")
	}
}

func TestRegistryRejectsCapabilityMismatchBeforeHandler(t *testing.T) {
	called := false
	r := testRegistry(t, &called)
	_, err := r.CallTool(executionContext("repository.write"), "read_file", nil)
	var mismatch *CapabilityMismatchError
	if !errors.As(err, &mismatch) {
		t.Fatalf("error = %v, want CapabilityMismatchError", err)
	}
	if mismatch.Tool != "read_file" || mismatch.DeclaredCapability != "repository.write" {
		t.Fatalf("unexpected mismatch metadata: %+v", mismatch)
	}
	if called {
		t.Fatal("mismatched tool handler must not run")
	}
}

func TestRegistryKeepsLegacyTransportCompatibilityWithoutExecutionContext(t *testing.T) {
	called := false
	r := testRegistry(t, &called)
	if _, err := r.CallTool(context.Background(), "read_file", nil); err != nil {
		t.Fatalf("legacy call: %v", err)
	}
	if !called {
		t.Fatal("legacy tool handler was not called")
	}
}

func TestBuiltinToolsDeclareStableCapabilities(t *testing.T) {
	r := New("http://knowledge.test", "http://gateway.test")
	want := map[string]string{
		"knowledge_search": "knowledge.search",
		"list_agents":      "agent.read",
		"call_agent":       "agent.dispatch",
		"list_sessions":    "session.read",
		"create_workflow":  "workflow.create",
		"ingest_document":  "document.ingest",
		"system_health":    "platform.health",
	}
	if len(r.tools) != len(want) {
		t.Fatalf("registered tools = %d, want %d", len(r.tools), len(want))
	}
	for name, capability := range want {
		tool, ok := r.tools[name]
		if !ok {
			t.Fatalf("tool %q is not registered", name)
		}
		if tool.Capability != capability {
			t.Fatalf("tool %q capability = %q, want %q", name, tool.Capability, capability)
		}
	}
}

func TestRegistryRejectsIncompleteOrDuplicateBindings(t *testing.T) {
	valid := RegisteredTool{
		Definition: protocol.ToolDefinition{Name: "read_file"},
		Capability: "repository.read",
		Handler: func(context.Context, map[string]any) (*protocol.ToolCallResult, error) {
			return &protocol.ToolCallResult{}, nil
		},
	}
	tests := []struct {
		name  string
		setup func(*Registry)
		tool  RegisteredTool
	}{
		{name: "missing capability", tool: RegisteredTool{Definition: valid.Definition, Handler: valid.Handler}},
		{name: "missing handler", tool: RegisteredTool{Definition: valid.Definition, Capability: valid.Capability}},
		{name: "duplicate", setup: func(r *Registry) { r.registerTool(valid) }, tool: valid},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			r := &Registry{tools: make(map[string]RegisteredTool)}
			if test.setup != nil {
				test.setup(r)
			}
			defer func() {
				if recover() == nil {
					t.Fatal("registration must fail closed")
				}
			}()
			r.registerTool(test.tool)
		})
	}
}

func TestCallAgentPropagatesAuthenticatedTenantActorAndCredential(t *testing.T) {
	var payload map[string]any
	var authorization string
	var decodeErr error
	downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authorization = r.Header.Get("Authorization")
		decodeErr = json.NewDecoder(r.Body).Decode(&payload)
		if decodeErr != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"published":true}`))
	}))
	defer downstream.Close()

	identity, wantAuthorization := authenticatedIdentityContext(t)
	ctx := transport.WithRequestContext(identity, transport.MCPRequestContext{
		MissionID:  "mission-1",
		WorkUnitID: "work-unit-1",
		Attempt:    1,
		Capability: "agent.dispatch",
		Scope:      map[string]any{},
		TraceID:    "trace-real",
	})
	r := New("http://knowledge.test", downstream.URL)
	_, err := r.CallTool(ctx, "call_agent", map[string]any{
		"agent_id":   "reviewer",
		"message":    "review this",
		"session_id": "session-real",
	})
	if err != nil {
		t.Fatalf("call agent: %v", err)
	}
	if decodeErr != nil {
		t.Fatalf("decode publish body: %v", decodeErr)
	}
	if payload["tenant_id"] != "tenant-real" || payload["actor_id"] != "actor-real" {
		t.Fatalf("downstream identity = tenant:%v actor:%v", payload["tenant_id"], payload["actor_id"])
	}
	if payload["trace_id"] != "trace-real" {
		t.Fatalf("trace_id = %v, want trace-real", payload["trace_id"])
	}
	if authorization != wantAuthorization {
		t.Fatal("verified credential was not forwarded")
	}
}

func TestIngestDocumentPropagatesAuthenticatedTenantAndActor(t *testing.T) {
	var payload map[string]any
	var decodeErr error
	downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		decodeErr = json.NewDecoder(r.Body).Decode(&payload)
		if decodeErr != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"accepted":true}`))
	}))
	defer downstream.Close()

	identity, _ := authenticatedIdentityContext(t)
	ctx := transport.WithRequestContext(identity, transport.MCPRequestContext{
		MissionID: "mission-1", WorkUnitID: "work-unit-1", Attempt: 1,
		Capability: "document.ingest", Scope: map[string]any{},
	})
	r := New(downstream.URL, "http://gateway.test")
	_, err := r.CallTool(ctx, "ingest_document", map[string]any{
		"content": "verified content",
		"title":   "evidence.md",
	})
	if err != nil {
		t.Fatalf("ingest document: %v", err)
	}
	if decodeErr != nil {
		t.Fatalf("decode ingest body: %v", decodeErr)
	}
	metadata, ok := payload["metadata"].(map[string]any)
	if !ok {
		t.Fatalf("metadata = %T, want object", payload["metadata"])
	}
	if payload["tenant_id"] != "tenant-real" || metadata["actor_id"] != "actor-real" {
		t.Fatalf("downstream identity = tenant:%v metadata:%v", payload["tenant_id"], metadata)
	}
}

func TestTenantScopedToolsFailBeforeNetworkWithoutAuthenticatedIdentity(t *testing.T) {
	called := false
	downstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		called = true
	}))
	defer downstream.Close()
	r := New(downstream.URL, downstream.URL)
	tests := []struct {
		name       string
		capability string
		arguments  map[string]any
	}{
		{name: "call_agent", capability: "agent.dispatch", arguments: map[string]any{"agent_id": "reviewer", "message": "review"}},
		{name: "ingest_document", capability: "document.ingest", arguments: map[string]any{"content": "content", "title": "doc"}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := r.CallTool(executionContext(test.capability), test.name, test.arguments)
			if !errors.Is(err, ErrTenantIdentityRequired) {
				t.Fatalf("error = %v, want ErrTenantIdentityRequired", err)
			}
		})
	}
	if called {
		t.Fatal("downstream must not be called without authenticated identity")
	}
}

func TestCallAgentRequiresVerifiedDownstreamCredential(t *testing.T) {
	ctx := iam.WithTenantContext(executionContext("agent.dispatch"), iam.TenantContext{
		TenantID: "tenant-real",
		UserID:   "actor-real",
	})
	r := New("http://knowledge.test", "http://gateway.test")
	_, err := r.CallTool(ctx, "call_agent", map[string]any{"agent_id": "reviewer", "message": "review"})
	if !errors.Is(err, ErrDownstreamCredentialRequired) {
		t.Fatalf("error = %v, want ErrDownstreamCredentialRequired", err)
	}
}
