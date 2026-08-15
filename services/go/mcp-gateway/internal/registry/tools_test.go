package registry

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
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
		"call_agent":       "agent.delegate",
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

func TestBuiltinResourcesDeclareStableCapabilities(t *testing.T) {
	r := New("http://knowledge.test", "http://gateway.test")
	want := map[string]string{
		"agenthub://knowledge/collections": "knowledge.read",
		"agenthub://agents/manifest":       "agent.read",
		"agenthub://templates/catalog":     "template.read",
		"agenthub://workspaces/list":       "workspace.read",
	}
	if len(r.resources) != len(want) {
		t.Fatalf("registered resources = %d, want %d", len(r.resources), len(want))
	}
	for _, resource := range r.resources {
		capability, ok := want[resource.Definition.URI]
		if !ok {
			t.Fatalf("resource %q is not expected", resource.Definition.URI)
		}
		if resource.Capability != capability {
			t.Fatalf("resource %q capability = %q, want %q", resource.Definition.URI, resource.Capability, capability)
		}
	}
}

func TestRegistryRejectsIncompleteOrDuplicateResourceBindings(t *testing.T) {
	valid := RegisteredResource{
		Definition: protocol.ResourceDefinition{URI: "agenthub://repository/files"},
		Capability: "repository.read",
	}
	tests := []struct {
		name     string
		setup    func(*Registry)
		resource RegisteredResource
	}{
		{name: "missing uri", resource: RegisteredResource{Capability: valid.Capability}},
		{name: "missing capability", resource: RegisteredResource{Definition: valid.Definition}},
		{name: "duplicate", setup: func(r *Registry) { r.registerResource(valid) }, resource: valid},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			r := &Registry{resources: make([]RegisteredResource, 0)}
			if test.setup != nil {
				test.setup(r)
			}
			defer func() {
				if recover() == nil {
					t.Fatal("resource registration must fail closed")
				}
			}()
			r.registerResource(test.resource)
		})
	}
}

func TestRegistryRejectsResourceCapabilityMismatchBeforeNetwork(t *testing.T) {
	called := false
	downstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		called = true
	}))
	defer downstream.Close()

	_, err := New(downstream.URL, downstream.URL).ReadResource(
		executionContext("workspace.read"),
		"agenthub://agents/manifest",
	)
	var mismatch *ResourceCapabilityMismatchError
	if !errors.As(err, &mismatch) {
		t.Fatalf("error = %v, want ResourceCapabilityMismatchError", err)
	}
	if mismatch.URI != "agenthub://agents/manifest" || mismatch.DeclaredCapability != "workspace.read" {
		t.Fatalf("unexpected mismatch metadata: %+v", mismatch)
	}
	if called {
		t.Fatal("mismatched resource capability must fail before network")
	}
}

func TestResourceCapabilityMismatchReturnsJSONRPCError(t *testing.T) {
	called := false
	downstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		called = true
	}))
	defer downstream.Close()
	handler := protocol.NewHandler(
		protocol.ServerInfo{Name: "test", Version: "test"},
		protocol.ServerCapabilities{Resources: &protocol.ResourcesCapability{}},
		New(downstream.URL, downstream.URL),
	)

	responses, err := handler.Dispatch(
		executionContext("workspace.read"),
		json.RawMessage(`{"jsonrpc":"2.0","id":1,"method":"resources/read","params":{"uri":"agenthub://agents/manifest"}}`),
	)
	if err != nil {
		t.Fatalf("dispatch resource read: %v", err)
	}
	if len(responses) != 1 {
		t.Fatalf("responses = %d, want 1", len(responses))
	}
	response, ok := responses[0].(protocol.ErrorResponse)
	if !ok || response.Error.Code != protocol.ErrInternalError {
		t.Fatalf("response = %#v, want JSON-RPC internal error", responses[0])
	}
	if called {
		t.Fatal("JSON-RPC mismatch must fail before network")
	}
}

func TestAgentManifestResourcePropagatesAuthenticatedTenantAndCredential(t *testing.T) {
	var tenantID, authorization string
	downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tenantID = r.URL.Query().Get("tenant_id")
		authorization = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"tenant_id":"tenant-real","agents":[]}`))
	}))
	defer downstream.Close()

	identity, wantAuthorization := authenticatedIdentityContext(t)
	ctx := transport.WithRequestContext(identity, transport.MCPRequestContext{
		MissionID: "mission-1", WorkUnitID: "work-unit-1", Attempt: 1,
		Capability: "agent.read", Scope: map[string]any{},
	})
	result, err := New("http://knowledge.test", downstream.URL).ReadResource(ctx, "agenthub://agents/manifest")
	if err != nil {
		t.Fatalf("read agent manifest: %v", err)
	}
	if tenantID != "tenant-real" {
		t.Fatalf("tenant_id = %q, want authenticated tenant", tenantID)
	}
	if authorization != wantAuthorization {
		t.Fatal("verified credential was not forwarded")
	}
	if len(result.Contents) != 1 || !json.Valid([]byte(result.Contents[0].Text)) {
		t.Fatalf("unexpected resource result: %+v", result)
	}
}

func TestAgentManifestResourceFailsBeforeNetworkWithoutIdentityOrCredential(t *testing.T) {
	called := false
	downstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		called = true
	}))
	defer downstream.Close()
	r := New("http://knowledge.test", downstream.URL)

	_, err := r.ReadResource(executionContext("agent.read"), "agenthub://agents/manifest")
	if !errors.Is(err, ErrTenantIdentityRequired) {
		t.Fatalf("missing identity error = %v, want ErrTenantIdentityRequired", err)
	}
	ctx := iam.WithTenantContext(executionContext("agent.read"), iam.TenantContext{
		TenantID: "tenant-real",
		UserID:   "actor-real",
	})
	_, err = r.ReadResource(ctx, "agenthub://agents/manifest")
	if !errors.Is(err, ErrDownstreamCredentialRequired) {
		t.Fatalf("missing credential error = %v, want ErrDownstreamCredentialRequired", err)
	}
	if called {
		t.Fatal("agent manifest must fail before network without identity or credential")
	}
}

func TestAgentManifestResourceRejectsDownstreamFailureAndInvalidJSON(t *testing.T) {
	tests := []struct {
		name   string
		status int
		body   string
	}{
		{name: "non-success status", status: http.StatusForbidden, body: `{"error":"forbidden"}`},
		{name: "invalid json", status: http.StatusOK, body: `not-json`},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(test.status)
				_, _ = w.Write([]byte(test.body))
			}))
			defer downstream.Close()
			identity, _ := authenticatedIdentityContext(t)
			ctx := transport.WithRequestContext(identity, transport.MCPRequestContext{
				MissionID: "mission-1", WorkUnitID: "work-unit-1", Attempt: 1,
				Capability: "agent.read", Scope: map[string]any{},
			})
			if _, err := New("http://knowledge.test", downstream.URL).ReadResource(ctx, "agenthub://agents/manifest"); err == nil {
				t.Fatal("downstream failure must fail the resource read")
			}
		})
	}
}

func TestResourceReadKeepsLegacyTransportCompatibility(t *testing.T) {
	downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"agents":[]}`))
	}))
	defer downstream.Close()
	identity, _ := authenticatedIdentityContext(t)

	if _, err := New("http://knowledge.test", downstream.URL).ReadResource(identity, "agenthub://agents/manifest"); err != nil {
		t.Fatalf("legacy resource read: %v", err)
	}
}

func TestJSONResourceRejectsDownstreamFailureAndInvalidJSON(t *testing.T) {
	tests := []struct {
		name   string
		status int
		body   string
	}{
		{name: "non-success status", status: http.StatusBadGateway, body: `{"error":"unavailable"}`},
		{name: "invalid json", status: http.StatusOK, body: `not-json`},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(test.status)
				_, _ = w.Write([]byte(test.body))
			}))
			defer downstream.Close()
			if _, err := New(downstream.URL, "http://gateway.test").ReadResource(
				executionContext("knowledge.read"),
				"agenthub://knowledge/collections",
			); err == nil {
				t.Fatal("downstream failure must fail the resource read")
			}
		})
	}
}

func TestCallAgentDelegatesThroughMissionControl(t *testing.T) {
	var payload map[string]any
	var requestPath string
	var authorization string
	downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requestPath = r.URL.Path
		authorization = r.Header.Get("Authorization")
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			http.Error(w, "invalid body", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"child-1","status":"PENDING"}`))
	}))
	defer downstream.Close()
	identity, _ := authenticatedIdentityContext(t)
	ctx := transport.WithRequestContext(identity, transport.MCPRequestContext{
		MissionID: "mission-1", WorkUnitID: "work-unit-1", Attempt: 1,
		Capability: "agent.delegate", Scope: map[string]any{},
	})
	r := New("http://knowledge.test", downstream.URL)
	result, err := r.CallTool(ctx, "call_agent", map[string]any{
		"id":                    "child-1",
		"agent_id":              "reviewer",
		"lease_id":              "lease-parent-1",
		"input_refs":            []any{map[string]any{"id": "artifact-1", "digest": "sha256:abc"}},
		"expected_outputs":      []any{map[string]any{"kind": "review", "required": true}},
		"required_capabilities": []any{"repository.read"},
	})
	if err != nil {
		t.Fatalf("call_agent: %v", err)
	}
	if result == nil || result.IsError {
		t.Fatalf("result = %+v, want accepted delegation", result)
	}
	if requestPath != "/api/v1/missions/mission-1/work-units/work-unit-1/delegations" {
		t.Fatalf("request path = %q", requestPath)
	}
	if authorization == "" {
		t.Fatal("delegation must forward authenticated credential")
	}
	if payload["lease_id"] != "lease-parent-1" || payload["agent_id"] != "reviewer" {
		t.Fatalf("payload omitted delegation identity: %+v", payload)
	}
	if _, err := r.CallTool(context.Background(), "call_agent", map[string]any{}); !errors.Is(err, ErrExecutionContextRequired) {
		t.Fatalf("missing execution context error = %v", err)
	}
}

func TestCallAgentSurfacesMissionConflictAsMCPError(t *testing.T) {
	downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, `{"detail":"delegation id already exists with different immutable fields"}`, http.StatusConflict)
	}))
	defer downstream.Close()
	identity, _ := authenticatedIdentityContext(t)
	ctx := transport.WithRequestContext(identity, transport.MCPRequestContext{
		MissionID: "mission-1", WorkUnitID: "work-unit-1", Attempt: 1,
		Capability: "agent.delegate", Scope: map[string]any{},
	})
	r := New("http://knowledge.test", downstream.URL)
	result, err := r.CallTool(ctx, "call_agent", map[string]any{
		"id":                    "child-1",
		"agent_id":              "reviewer",
		"lease_id":              "lease-parent-1",
		"input_refs":            []any{map[string]any{"id": "artifact-1", "digest": "sha256:abc"}},
		"expected_outputs":      []any{},
		"required_capabilities": []any{},
	})
	if err != nil {
		t.Fatalf("call_agent conflict: %v", err)
	}
	if result == nil || !result.IsError {
		t.Fatalf("result = %+v, want MCP error result", result)
	}
	if len(result.Content) != 1 || !strings.Contains(result.Content[0].Text, "HTTP 409") {
		t.Fatalf("conflict content = %+v", result.Content)
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

func TestListAgentsPropagatesAuthenticatedTenantAndCredential(t *testing.T) {
	var tenantID, authorization string
	downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tenantID = r.URL.Query().Get("tenant_id")
		authorization = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"agents":[]}`))
	}))
	defer downstream.Close()

	identity, wantAuthorization := authenticatedIdentityContext(t)
	ctx := transport.WithRequestContext(identity, transport.MCPRequestContext{
		MissionID: "mission-1", WorkUnitID: "work-unit-1", Attempt: 1,
		Capability: "agent.read", Scope: map[string]any{},
	})
	result, err := New("http://knowledge.test", downstream.URL).CallTool(ctx, "list_agents", map[string]any{
		"tenant_id": "tenant-attacker",
	})
	if err != nil {
		t.Fatalf("list agents: %v", err)
	}
	if result.IsError {
		t.Fatalf("list agents returned tool error: %+v", result)
	}
	if tenantID != "tenant-real" {
		t.Fatalf("tenant_id = %q, want authenticated tenant", tenantID)
	}
	if authorization != wantAuthorization {
		t.Fatal("verified credential was not forwarded")
	}
}

func TestListAgentsRejectsDownstreamFailureAndInvalidJSON(t *testing.T) {
	tests := []struct {
		name   string
		status int
		body   string
	}{
		{name: "non-success status", status: http.StatusForbidden, body: `{"error":"forbidden"}`},
		{name: "invalid json", status: http.StatusOK, body: `not-json`},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(test.status)
				_, _ = w.Write([]byte(test.body))
			}))
			defer downstream.Close()
			identity, _ := authenticatedIdentityContext(t)
			ctx := transport.WithRequestContext(identity, transport.MCPRequestContext{
				MissionID: "mission-1", WorkUnitID: "work-unit-1", Attempt: 1,
				Capability: "agent.read", Scope: map[string]any{},
			})
			result, err := New("http://knowledge.test", downstream.URL).CallTool(ctx, "list_agents", nil)
			if err != nil {
				t.Fatalf("list agents: %v", err)
			}
			if !result.IsError {
				t.Fatal("downstream failure must be returned as a tool error")
			}
		})
	}
}

func TestListSessionsPropagatesAuthenticatedTenantLimitAndCredential(t *testing.T) {
	var tenantID, limit, authorization string
	downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tenantID = r.URL.Query().Get("tenant_id")
		limit = r.URL.Query().Get("limit")
		authorization = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"sessions":[]}`))
	}))
	defer downstream.Close()

	identity, wantAuthorization := authenticatedIdentityContext(t)
	ctx := transport.WithRequestContext(identity, transport.MCPRequestContext{
		MissionID: "mission-1", WorkUnitID: "work-unit-1", Attempt: 1,
		Capability: "session.read", Scope: map[string]any{},
	})
	r := New("http://knowledge.test", downstream.URL)
	result, err := r.CallTool(ctx, "list_sessions", map[string]any{
		"limit":     float64(500),
		"tenant_id": "tenant-attacker",
	})
	if err != nil {
		t.Fatalf("list sessions: %v", err)
	}
	if result.IsError {
		t.Fatalf("list sessions returned tool error: %+v", result)
	}
	if tenantID != "tenant-real" {
		t.Fatalf("tenant_id = %q, want authenticated tenant", tenantID)
	}
	if limit != "50" {
		t.Fatalf("limit = %q, want 50", limit)
	}
	if authorization != wantAuthorization {
		t.Fatal("verified credential was not forwarded")
	}
}

func TestListSessionsNormalizesLimit(t *testing.T) {
	tests := []struct {
		name  string
		value any
		want  string
	}{
		{name: "missing", want: "10"},
		{name: "zero", value: 0, want: "10"},
		{name: "negative", value: int64(-5), want: "10"},
		{name: "maximum", value: float64(50), want: "50"},
		{name: "over maximum", value: float64(51), want: "50"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var got string
			downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				got = r.URL.Query().Get("limit")
				_, _ = w.Write([]byte(`{"sessions":[]}`))
			}))
			defer downstream.Close()

			identity, _ := authenticatedIdentityContext(t)
			ctx := transport.WithRequestContext(identity, transport.MCPRequestContext{
				MissionID: "mission-1", WorkUnitID: "work-unit-1", Attempt: 1,
				Capability: "session.read", Scope: map[string]any{},
			})
			arguments := map[string]any{}
			if test.value != nil {
				arguments["limit"] = test.value
			}
			result, err := New("http://knowledge.test", downstream.URL).CallTool(ctx, "list_sessions", arguments)
			if err != nil {
				t.Fatalf("list sessions: %v", err)
			}
			if result.IsError {
				t.Fatalf("list sessions returned tool error: %+v", result)
			}
			if got != test.want {
				t.Fatalf("limit = %q, want %q", got, test.want)
			}
		})
	}
}

func TestListSessionsRejectsDownstreamFailureAndInvalidJSON(t *testing.T) {
	tests := []struct {
		name   string
		status int
		body   string
	}{
		{name: "non-success status", status: http.StatusForbidden, body: `{"error":"forbidden"}`},
		{name: "invalid json", status: http.StatusOK, body: `not-json`},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(test.status)
				_, _ = w.Write([]byte(test.body))
			}))
			defer downstream.Close()
			identity, _ := authenticatedIdentityContext(t)
			ctx := transport.WithRequestContext(identity, transport.MCPRequestContext{
				MissionID: "mission-1", WorkUnitID: "work-unit-1", Attempt: 1,
				Capability: "session.read", Scope: map[string]any{},
			})
			result, err := New("http://knowledge.test", downstream.URL).CallTool(ctx, "list_sessions", nil)
			if err != nil {
				t.Fatalf("list sessions: %v", err)
			}
			if !result.IsError {
				t.Fatal("downstream failure must be returned as a tool error")
			}
		})
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
		{name: "ingest_document", capability: "document.ingest", arguments: map[string]any{"content": "content", "title": "doc"}},
		{name: "list_sessions", capability: "session.read", arguments: map[string]any{}},
		{name: "list_agents", capability: "agent.read", arguments: map[string]any{}},
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

func TestListSessionsRequiresVerifiedDownstreamCredential(t *testing.T) {
	ctx := iam.WithTenantContext(executionContext("session.read"), iam.TenantContext{
		TenantID: "tenant-real",
		UserID:   "actor-real",
	})
	r := New("http://knowledge.test", "http://gateway.test")
	_, err := r.CallTool(ctx, "list_sessions", nil)
	if !errors.Is(err, ErrDownstreamCredentialRequired) {
		t.Fatalf("error = %v, want ErrDownstreamCredentialRequired", err)
	}
}

func TestListAgentsRequiresVerifiedDownstreamCredential(t *testing.T) {
	ctx := iam.WithTenantContext(executionContext("agent.read"), iam.TenantContext{
		TenantID: "tenant-real",
		UserID:   "actor-real",
	})
	r := New("http://knowledge.test", "http://gateway.test")
	_, err := r.CallTool(ctx, "list_agents", nil)
	if !errors.Is(err, ErrDownstreamCredentialRequired) {
		t.Fatalf("error = %v, want ErrDownstreamCredentialRequired", err)
	}
}
