// Package auth contains MCP Gateway authorization composition. JWT parsing and
// claim definitions remain owned by shared/iam; this package only intersects
// the authenticated principal with the stateless MCP execution declaration.
package auth

import (
	"context"
	"net/http"
	"strings"

	"github.com/agenthub/mcp-gateway/internal/transport"
	"github.com/agenthub/platform/shared/iam"
)

// Middleware composes the shared IAM JWT middleware with the MCP authorizer.
// Unlike the compatibility WebSocket routes, stateless HTTP RPC requires a
// standard Authorization header and never accepts a query-token fallback.
func Middleware(issuer *iam.TokenIssuer, next http.Handler, onDeny func(*http.Request, string)) http.Handler {
	authenticated := iam.AuthMiddleware(issuer, nil, onDeny)(next)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.TrimSpace(r.Header.Get("Authorization")) == "" {
			http.Error(w, "authorization header is required", http.StatusUnauthorized)
			return
		}
		authenticated.ServeHTTP(w, r)
	})
}

// AuthorizeMCP enforces the identity side of a stateless MCP tool call.
// X-AgentHub capability headers are execution metadata, never authentication
// proof. Contract scope may narrow access through required_scope and tenant_id
// fields, but cannot widen the principal's IAM grants.
func AuthorizeMCP(ctx context.Context, request transport.MCPRequestContext) error {
	principal, ok := iam.FromContext(ctx)
	if !ok {
		return &transport.AuthorizationError{
			Status:  http.StatusUnauthorized,
			Message: "MCP authorization context is missing",
		}
	}
	if !principal.DevMode && principal.TenantID == "" {
		return &transport.AuthorizationError{
			Status:  http.StatusForbidden,
			Message: "MCP principal tenant is missing",
		}
	}
	if !principal.HasScope(iam.ScopeToolExecute) {
		return &transport.AuthorizationError{
			Status:  http.StatusForbidden,
			Message: "MCP principal lacks tool:execute",
		}
	}
	if requiredScope, ok := request.Scope["required_scope"].(string); ok &&
		requiredScope != "" && !principal.HasScope(requiredScope) {
		return &transport.AuthorizationError{
			Status:  http.StatusForbidden,
			Message: "MCP principal lacks required capability scope",
		}
	}
	if targetTenant, ok := request.Scope["tenant_id"].(string); ok &&
		targetTenant != "" && !iam.EnforceTenantScope(ctx, targetTenant) {
		return &transport.AuthorizationError{
			Status:  http.StatusForbidden,
			Message: "MCP capability scope crosses tenant boundary",
		}
	}
	return nil
}
