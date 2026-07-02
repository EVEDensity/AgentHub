package iam

import "context"

// ctxKey is an unexported type so no other package can collide with the
// TenantContext stored in a context.Context.
type ctxKey struct{}

// TenantContext is the authenticated principal's identity, propagated through
// the request context after the JWT middleware verifies the token. Every
// downstream handler / NATS subscriber reads it to enforce tenant isolation:
// data lookups are scoped by TenantID, authorization decisions use Roles /
// Scopes / TenantRole.
type TenantContext struct {
	TenantID   string
	UserID     string
	SessionID  string
	Roles      []string
	Scopes     []string
	TenantRole string
	// DevMode indicates the request arrived without a real JWT (dev mode).
	// Handlers may relax tenant scoping for local development but must still
	// tag emitted events with a synthetic tenant id.
	DevMode bool
}

// FromClaims builds a TenantContext from verified JWT claims.
func FromClaims(c *Claims, devMode bool) TenantContext {
	tc := TenantContext{
		TenantID:   c.TenantID,
		UserID:     c.UserID,
		SessionID:  c.SessionID,
		TenantRole: c.TenantRole,
		DevMode:    devMode,
	}
	if len(c.Roles) > 0 {
		tc.Roles = append([]string(nil), c.Roles...)
	}
	if len(c.Scopes) > 0 {
		tc.Scopes = append([]string(nil), c.Scopes...)
	}
	return tc
}

// WithTenantContext stores the TenantContext in the context.Context so handlers
// downstream of the auth middleware can retrieve it.
func WithTenantContext(ctx context.Context, tc TenantContext) context.Context {
	return context.WithValue(ctx, ctxKey{}, tc)
}

// FromContext retrieves the TenantContext. The second return is false when no
// auth middleware ran (e.g. internal service-to-service calls); callers should
// treat that as unauthenticated and deny tenant-scoped operations.
func FromContext(ctx context.Context) (TenantContext, bool) {
	tc, ok := ctx.Value(ctxKey{}).(TenantContext)
	return tc, ok
}

// HasScope reports whether the tenant context grants the named scope. In dev
// mode (no scopes populated) every scope check passes so local development is
// not blocked by an empty policy. SuperAdmin (or any scope set containing
// ScopeAll) grants every scope without listing them individually.
func (tc TenantContext) HasScope(scope string) bool {
	if tc.DevMode && len(tc.Scopes) == 0 {
		return true
	}
	// SuperAdmin role implies ScopeAll — check roles first so a token that
	// carries the role but not the expanded scope set still passes.
	if tc.HasRole(RoleSuperAdmin) {
		return true
	}
	for _, s := range tc.Scopes {
		if s == scope || s == ScopeAll {
			return true
		}
	}
	return false
}

// HasRole reports whether the tenant context carries the named role. In dev
// mode every role check passes.
func (tc TenantContext) HasRole(role string) bool {
	if tc.DevMode && len(tc.Roles) == 0 {
		return true
	}
	for _, r := range tc.Roles {
		if r == role || r == RoleSuperAdmin {
			return true
		}
	}
	return false
}
