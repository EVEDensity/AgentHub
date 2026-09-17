package auth

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/agenthub/mcp-gateway/internal/transport"
	"github.com/agenthub/platform/shared/iam"
)

func request(scope map[string]any) transport.MCPRequestContext {
	return transport.MCPRequestContext{
		MissionID:  "mission-1",
		WorkUnitID: "work-unit-1",
		Attempt:    1,
		Capability: "repository.read",
		Scope:      scope,
	}
}

func withPrincipal(tc iam.TenantContext) context.Context {
	return iam.WithTenantContext(context.Background(), tc)
}

func TestAuthorizeMCPRequiresAuthenticatedPrincipal(t *testing.T) {
	err := AuthorizeMCP(context.Background(), request(nil))
	assertAuthorization(t, err, 401, "MCP authorization context is missing")
}

func TestAuthorizeMCPIntersectsToolScopeAndCapabilityScope(t *testing.T) {
	principal := iam.TenantContext{TenantID: "tenant-1", UserID: "actor-1", Scopes: []string{iam.ScopeToolExecute}}
	if err := AuthorizeMCP(withPrincipal(principal), request(map[string]any{"required_scope": iam.ScopeDocRead})); err == nil {
		t.Fatal("expected required capability scope to be denied")
	} else {
		assertAuthorization(t, err, 403, "MCP principal lacks required capability scope")
	}

	if err := AuthorizeMCP(withPrincipal(principal), request(map[string]any{"tenant_id": "tenant-2"})); err == nil {
		t.Fatal("expected cross-tenant capability scope to be denied")
	} else {
		assertAuthorization(t, err, 403, "MCP capability scope crosses tenant boundary")
	}
}

func TestAuthorizeMCPAllowsIntersection(t *testing.T) {
	principal := iam.TenantContext{
		TenantID: "tenant-1",
		UserID:   "actor-1",
		Scopes:   []string{iam.ScopeToolExecute, iam.ScopeDocRead},
	}
	if err := AuthorizeMCP(withPrincipal(principal), request(map[string]any{
		"required_scope": iam.ScopeDocRead,
		"tenant_id":      "tenant-1",
	})); err != nil {
		t.Fatalf("expected authorized intersection, got %v", err)
	}
}

func TestAuthorizeMCPRejectsPrincipalWithoutToolExecute(t *testing.T) {
	err := AuthorizeMCP(withPrincipal(iam.TenantContext{TenantID: "tenant-1"}), request(nil))
	assertAuthorization(t, err, 403, "MCP principal lacks tool:execute")
}

func TestMiddlewareRequiresAuthorizationHeader(t *testing.T) {
	issuer := iam.NewTokenIssuer([]byte("test-secret-32bytes-xxxxxxxxxxxx"), "iam-service", time.Hour)
	handler := Middleware(issuer, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Fatal("handler must not run without Authorization")
	}), nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/mcp/rpc", strings.NewReader(`{}`)))
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusUnauthorized)
	}
}

func TestMiddlewareInjectsVerifiedTenantContext(t *testing.T) {
	issuer := iam.NewTokenIssuer([]byte("test-secret-32bytes-xxxxxxxxxxxx"), "iam-service", time.Hour)
	token, err := issuer.Issue(iam.Claims{
		TenantID: "tenant-1",
		UserID:   "actor-1",
		Scopes:   []string{iam.ScopeToolExecute},
	})
	if err != nil {
		t.Fatalf("issue token: %v", err)
	}
	called := false
	handler := Middleware(issuer, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		principal, ok := iam.FromContext(r.Context())
		if !ok || principal.TenantID != "tenant-1" || principal.UserID != "actor-1" {
			t.Fatalf("unexpected principal: %+v (ok=%v)", principal, ok)
		}
		authorization, ok := AuthorizationHeaderFromContext(r.Context())
		if !ok || authorization != "Bearer "+token {
			t.Fatalf("verified authorization was not propagated")
		}
		w.WriteHeader(http.StatusNoContent)
	}), nil)
	req := httptest.NewRequest(http.MethodPost, "/mcp/rpc", strings.NewReader(`{}`))
	req.Header.Set("Authorization", "Bearer "+token)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, req)
	if !called || recorder.Code != http.StatusNoContent {
		t.Fatalf("called=%v status=%d", called, recorder.Code)
	}
}

func TestAuthenticatedStatelessRPCPropagatesIdentityAndExecutionContext(t *testing.T) {
	issuer := iam.NewTokenIssuer([]byte("test-secret-32bytes-xxxxxxxxxxxx"), "iam-service", time.Hour)
	token, err := issuer.Issue(iam.Claims{
		TenantID: "tenant-1",
		UserID:   "actor-1",
		Scopes:   []string{iam.ScopeToolExecute, iam.ScopeDocRead},
	})
	if err != nil {
		t.Fatalf("issue token: %v", err)
	}
	var principal iam.TenantContext
	var execution transport.MCPRequestContext
	dispatch := func(ctx context.Context, _ json.RawMessage) ([]json.RawMessage, error) {
		principal, _ = iam.FromContext(ctx)
		execution, _ = transport.RequestContextFromContext(ctx)
		return []json.RawMessage{json.RawMessage(`{"jsonrpc":"2.0","id":1,"result":{}}`)}, nil
	}
	handler := Middleware(
		issuer,
		transport.NewStatelessHTTPHandlerWithAuthorizer(dispatch, AuthorizeMCP),
		nil,
	)
	req := httptest.NewRequest(http.MethodPost, "/mcp/rpc", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/call"}`))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("X-AgentHub-Mission-Id", "mission-1")
	req.Header.Set("X-AgentHub-Work-Unit-Id", "work-unit-1")
	req.Header.Set("X-AgentHub-Attempt", "1")
	req.Header.Set("X-AgentHub-Capability", "knowledge.read")
	req.Header.Set("X-AgentHub-Capability-Scope", `{"required_scope":"document:read","tenant_id":"tenant-1"}`)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, req)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d body=%q", recorder.Code, recorder.Body.String())
	}
	if principal.TenantID != "tenant-1" || principal.UserID != "actor-1" {
		t.Fatalf("identity context = %+v", principal)
	}
	if execution.MissionID != "mission-1" || execution.WorkUnitID != "work-unit-1" {
		t.Fatalf("execution context = %+v", execution)
	}
}

func assertAuthorization(t *testing.T, err error, status int, message string) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected authorization error %d", status)
	}
	authErr, ok := err.(*transport.AuthorizationError)
	if !ok {
		t.Fatalf("error type = %T, want *transport.AuthorizationError", err)
	}
	if authErr.Status != status || authErr.Message != message {
		t.Fatalf("authorization error = %+v, want status=%d message=%q", authErr, status, message)
	}
}
