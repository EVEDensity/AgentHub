package iam

// ── Roles ──────────────────────────────────────────────────────────────
// Platform-wide role names. TenantRole in the JWT carries one of these
// (except SuperAdmin which is a cross-tenant break-glass role). Roles map to
// permission sets via the Policy; iam-service persists tenant-specific role
// overrides in platform_role_permissions.

const (
	RoleSuperAdmin    = "super_admin"    // cross-tenant break-glass; bypasses all checks
	RoleTenantAdmin   = "tenant_admin"   // tenant-scoped admin: manage members, roles, quotas
	RoleAgentOperator = "agent_operator" // P0.3: can execute high-risk tools, cannot manage members
	RoleMember        = "member"         // standard user: create sessions, run agents, use tools
	RoleViewer        = "viewer"         // read-only: observe sessions and audit logs
)

// ── Scopes ─────────────────────────────────────────────────────────────
// Fine-grained permission strings embedded in the JWT "scopes" claim. Scopes
// are the primary authorization primitive in the hot path — verifying a scope
// is an O(n) scan over a short slice, no DB round-trip. Roles expand to scope
// sets at token-issuance time so runtime checks stay local.

const (
	ScopeAll           = "*" // granted only to super_admin / tenant_admin
	ScopeSessionRead   = "session:read"
	ScopeSessionWrite  = "session:write"
	ScopeSessionCreate = "session:create"
	ScopeSessionDelete = "session:delete"
	ScopeAgentDispatch = "agent:dispatch"
	ScopeAgentRead     = "agent:read"
	ScopeToolExecute   = "tool:execute"
	ScopeToolApprove   = "tool:approve"
	ScopeMemoryRead    = "memory:read"
	ScopeMemoryWrite   = "memory:write"
	ScopeDocUpload     = "document:upload"
	ScopeDocRead       = "document:read"
	ScopeAuditRead     = "audit:read"
	ScopeTenantManage  = "tenant:manage"
	ScopeRoleManage    = "role:manage"
	ScopeBillingRead   = "billing:read"
	// ── P0.3: workspace + model management scopes ──
	ScopeWorkspaceAdmin = "workspace:admin" // manage workspace settings, ACL
	ScopeWorkspaceRead  = "workspace:read"  // view workspace content
	ScopeModelManage    = "model:manage"    // configure model providers, quotas
	ScopeMissionClaim   = "mission:claim"   // claim ready WorkUnits in an explicitly granted workspace
	ScopeMissionVerify  = "mission:verify"  // admit Evidence for WorkUnits in an explicitly granted workspace
)

// DefaultRoleScopes maps each built-in role to the scope set it grants. These
// are the system defaults; tenant_admin can narrow them per tenant via
// platform_role_permissions overrides (stored in PG, merged at token issuance).
var DefaultRoleScopes = map[string][]string{
	RoleSuperAdmin: {ScopeAll},
	RoleTenantAdmin: {
		ScopeSessionRead, ScopeSessionWrite, ScopeSessionCreate, ScopeSessionDelete,
		ScopeAgentDispatch, ScopeAgentRead,
		ScopeToolExecute, ScopeToolApprove,
		ScopeMemoryRead, ScopeMemoryWrite,
		ScopeDocUpload, ScopeDocRead,
		ScopeAuditRead, ScopeTenantManage, ScopeRoleManage, ScopeBillingRead,
		ScopeWorkspaceAdmin, ScopeWorkspaceRead, ScopeModelManage,
	},
	RoleAgentOperator: {
		// P0.3: agent operators can run agents, execute all tools (including
		// high-risk via tool:approve), manage workspaces and models, but
		// CANNOT manage tenant members, roles, or billing.
		ScopeSessionRead, ScopeSessionWrite, ScopeSessionCreate,
		ScopeAgentDispatch, ScopeAgentRead,
		ScopeToolExecute, ScopeToolApprove,
		ScopeMemoryRead, ScopeMemoryWrite,
		ScopeDocUpload, ScopeDocRead,
		ScopeWorkspaceAdmin, ScopeWorkspaceRead, ScopeModelManage,
	},
	RoleMember: {
		ScopeSessionRead, ScopeSessionWrite, ScopeSessionCreate,
		ScopeAgentDispatch, ScopeAgentRead,
		ScopeToolExecute,
		ScopeMemoryRead, ScopeMemoryWrite,
		ScopeDocUpload, ScopeDocRead,
		ScopeWorkspaceRead,
	},
	RoleViewer: {
		ScopeSessionRead, ScopeAgentRead, ScopeDocRead, ScopeAuditRead,
		ScopeWorkspaceRead,
	},
}

// ScopesForRoles expands a role list into the union of their default scopes.
// Tenant-specific overrides (applied by iam-service at token issuance) are
// already folded into the JWT scopes claim, so this helper is mainly for
// policy reloads and tests.
func ScopesForRoles(roles []string) []string {
	seen := map[string]struct{}{}
	out := make([]string, 0, 16)
	for _, r := range roles {
		for _, s := range DefaultRoleScopes[r] {
			if _, ok := seen[s]; ok {
				continue
			}
			seen[s] = struct{}{}
			out = append(out, s)
		}
	}
	return out
}

// Policy is the in-memory RBAC policy: a role -> scope-set map. A zero Policy
// denies everything except in dev mode. iam-service loads it from PG at startup
// and refreshes on role changes; ingress services receive scopes via the JWT
// so they do not need to hold a Policy themselves.
type Policy struct {
	roleScopes map[string]map[string]struct{}
}

// NewPolicy builds a Policy from a role -> scopes map. nil yields an empty
// (deny-all) policy.
func NewPolicy(roleScopes map[string][]string) *Policy {
	p := &Policy{roleScopes: make(map[string]map[string]struct{}, len(roleScopes))}
	for role, scopes := range roleScopes {
		set := make(map[string]struct{}, len(scopes))
		for _, s := range scopes {
			set[s] = struct{}{}
		}
		p.roleScopes[role] = set
	}
	return p
}

// DefaultPolicy returns a Policy seeded with DefaultRoleScopes.
func DefaultPolicy() *Policy { return NewPolicy(DefaultRoleScopes) }

// HasScope reports whether any of the given roles grants the scope. SuperAdmin
// (or any role carrying ScopeAll) grants every scope.
func (p *Policy) HasScope(roles []string, scope string) bool {
	for _, r := range roles {
		set, ok := p.roleScopes[r]
		if !ok {
			continue
		}
		if _, ok := set[ScopeAll]; ok {
			return true
		}
		if _, ok := set[scope]; ok {
			return true
		}
	}
	return false
}

// ScopesFor returns the union of scopes granted by the given roles under this
// policy. Duplicates are removed.
func (p *Policy) ScopesFor(roles []string) []string {
	seen := map[string]struct{}{}
	out := make([]string, 0, 16)
	for _, r := range roles {
		set, ok := p.roleScopes[r]
		if !ok {
			continue
		}
		for s := range set {
			if _, ok := seen[s]; ok {
				continue
			}
			seen[s] = struct{}{}
			out = append(out, s)
		}
	}
	return out
}
