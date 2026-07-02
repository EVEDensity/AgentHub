package iam

import (
	"context"
	"net/http"
	"strings"
)

// extractToken pulls the bearer token from the Authorization header, falling
// back to a "token" query parameter (used by WebSocket clients that cannot set
// headers on the upgrade request).
func extractToken(r *http.Request) string {
	if h := r.Header.Get("Authorization"); len(h) > 7 && strings.EqualFold(h[:7], "Bearer ") {
		return strings.TrimSpace(h[7:])
	}
	if t := r.URL.Query().Get("token"); t != "" {
		return t
	}
	return ""
}

// AuthMiddleware returns an http.Handler wrapper that verifies the JWT on
// every request and injects the TenantContext into the request context. When
// the issuer is in dev mode (no secret), the middleware injects an empty
// TenantContext flagged DevMode so handlers can still run locally.
//
// publicPrefixes are path prefixes exempt from auth (e.g. "/healthz", "/metrics",
// "/profile"). Requests to these paths skip verification entirely.
//
// onDeny, if non-nil, is called for every rejected request — callers use it to
// increment Prometheus counters or emit audit events.
func AuthMiddleware(issuer *TokenIssuer, publicPrefixes []string, onDeny func(r *http.Request, reason string)) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			for _, p := range publicPrefixes {
				if strings.HasPrefix(r.URL.Path, p) {
					next.ServeHTTP(w, r)
					return
				}
			}
			token := extractToken(r)
			claims, err := issuer.Verify(token)
			if err != nil {
				if onDeny != nil {
					onDeny(r, err.Error())
				}
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			tc := FromClaims(claims, issuer.IsDevMode())
			// In dev mode with no token, fall back to query-param tenant/user so
			// local curl calls still carry an identity for event tagging.
			if tc.DevMode {
				if tc.TenantID == "" {
					tc.TenantID = r.URL.Query().Get("tenant_id")
				}
				if tc.UserID == "" {
					tc.UserID = r.URL.Query().Get("user_id")
				}
			}
			r = r.WithContext(WithTenantContext(r.Context(), tc))
			next.ServeHTTP(w, r)
		})
	}
}

// RequireScope is a per-route guard that rejects the request with 403 when the
// authenticated TenantContext does not carry the named scope. It must run after
// AuthMiddleware. In dev mode (no scopes populated) the guard always passes.
func RequireScope(scope string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			tc, ok := FromContext(r.Context())
			if !ok {
				http.Error(w, "unauthenticated", http.StatusUnauthorized)
				return
			}
			if !tc.HasScope(scope) {
				http.Error(w, "forbidden: missing scope "+scope, http.StatusForbidden)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// RequireTenant is a guard that ensures a non-empty TenantID is present on the
// context. It is the minimum bar for any tenant-scoped operation.
func RequireTenant(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tc, ok := FromContext(r.Context())
		if !ok || tc.TenantID == "" {
			http.Error(w, "forbidden: tenant context required", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// EnforceTenantScope ensures a request targeting tenantID does not cross tenant
// boundaries: the authenticated principal's TenantID must match (unless dev mode
// or super_admin). Returns true when the access is allowed, false otherwise
// (the caller should respond 403). This is the defense-in-depth check services
// apply after parsing a tenant_id from the request body/path.
func EnforceTenantScope(ctx context.Context, targetTenantID string) bool {
	tc, ok := FromContext(ctx)
	if !ok {
		return false
	}
	if tc.DevMode {
		return true
	}
	if tc.HasRole(RoleSuperAdmin) {
		return true
	}
	if targetTenantID == "" {
		return true // caller will use the principal's own tenant downstream
	}
	return tc.TenantID == targetTenantID
}
