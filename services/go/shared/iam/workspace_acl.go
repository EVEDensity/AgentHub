package iam

// ─────────────────────────────────────────────────────────────────────
// workspace_acl.go — Workspace-level Access Control (P0.3)
// ─────────────────────────────────────────────────────────────────────
// While tenant-level RBAC (rbac.go) controls what a user can do across
// the entire tenant, workspace ACLs provide fine-grained per-workspace
// permissions. This is useful when:
//   - A member should admin one workspace but only view another
//   - An external collaborator gets read-only access to a specific workspace
//   - Different teams within a tenant isolate their workspaces
//
// The workspace ACL is an overlay on top of tenant RBAC: if a user has
// workspace:admin in their JWT scopes, they can admin any workspace in
// the tenant. The workspace ACL is for narrowing permissions below the
// tenant level, not for granting permissions the tenant role doesn't allow.
// ─────────────────────────────────────────────────────────────────────

import "sync"

// WorkspaceACL is a single user's role within a workspace.
type WorkspaceACL struct {
	WorkspaceID string   `json:"workspace_id"`
	UserID      string   `json:"user_id"`
	Role        string   `json:"role"`        // one of the Role* constants
	Permissions []string `json:"permissions"` // additional scope strings
}

// WorkspacePolicy is an in-memory workspace ACL store. iam-service loads
// it from PG at startup and refreshes on ACL changes.
type WorkspacePolicy struct {
	mu   sync.RWMutex
	acls map[string]map[string]WorkspaceACL // workspaceID -> userID -> ACL
}

// NewWorkspacePolicy creates an empty workspace policy.
func NewWorkspacePolicy() *WorkspacePolicy {
	return &WorkspacePolicy{acls: make(map[string]map[string]WorkspaceACL)}
}

// SetACL grants or updates a user's role within a workspace.
func (p *WorkspacePolicy) SetACL(acl WorkspaceACL) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if _, ok := p.acls[acl.WorkspaceID]; !ok {
		p.acls[acl.WorkspaceID] = make(map[string]WorkspaceACL)
	}
	p.acls[acl.WorkspaceID][acl.UserID] = acl
}

// RemoveACL removes a user's ACL from a workspace.
func (p *WorkspacePolicy) RemoveACL(workspaceID, userID string) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	if users, ok := p.acls[workspaceID]; ok {
		if _, exists := users[userID]; exists {
			delete(users, userID)
			return true
		}
	}
	return false
}

// GetACL returns the ACL for a user in a workspace. Returns ok=false if
// no workspace-specific ACL exists (caller should fall back to tenant RBAC).
func (p *WorkspacePolicy) GetACL(workspaceID, userID string) (WorkspaceACL, bool) {
	p.mu.RLock()
	defer p.mu.RUnlock()
	if users, ok := p.acls[workspaceID]; ok {
		acl, exists := users[userID]
		return acl, exists
	}
	return WorkspaceACL{}, false
}

// ListACL returns all ACLs for a workspace.
func (p *WorkspacePolicy) ListACL(workspaceID string) []WorkspaceACL {
	p.mu.RLock()
	defer p.mu.RUnlock()
	users, ok := p.acls[workspaceID]
	if !ok {
		return nil
	}
	out := make([]WorkspaceACL, 0, len(users))
	for _, acl := range users {
		out = append(out, acl)
	}
	return out
}

// CanExecute checks whether a user can execute a tool in a workspace.
// It first checks the workspace ACL; if none exists, it falls back to
// the tenant-level scope check.
//
// Parameters:
//   - workspaceID: the workspace identifier
//   - userID: the user identifier
//   - toolRisk: the risk level (from ClassifyTool / BuiltinToolRisk)
//   - tenantScopes: the user's tenant-level scopes (from JWT)
//   - tenantRoles: the user's tenant-level roles (from JWT)
func (p *WorkspacePolicy) CanExecute(
	workspaceID, userID, toolRisk string,
	tenantScopes []string,
	tenantRoles []string,
) bool {
	// If user has workspace:admin scope at tenant level, allow
	for _, s := range tenantScopes {
		if s == ScopeAll || s == ScopeWorkspaceAdmin {
			return true
		}
	}

	// Check workspace-specific ACL
	acl, ok := p.GetACL(workspaceID, userID)
	if !ok {
		// No workspace ACL — fall back to tenant RBAC
		return tenantHasToolExecute(tenantScopes, tenantRoles, toolRisk)
	}

	// Check explicit permissions first (override role-based restrictions)
	for _, perm := range acl.Permissions {
		if perm == ScopeAll || perm == ScopeToolExecute {
			// Critical tools still need tool:approve
			if toolRisk == RiskCritical {
				for _, p2 := range acl.Permissions {
					if p2 == ScopeToolApprove || p2 == ScopeAll {
						return true
					}
				}
				return false
			}
			return true
		}
	}

	// No explicit permission override — check role-based permission
	switch acl.Role {
	case RoleSuperAdmin, RoleTenantAdmin, RoleAgentOperator:
		return true
	case RoleMember:
		// Members can execute normal/low risk; high/critical needs confirmation
		return toolRisk == RiskLow || toolRisk == RiskNormal
	case RoleViewer:
		return false // viewers cannot execute tools
	}

	return false
}

// tenantHasToolExecute is the fallback when no workspace ACL exists.
// It uses the existing ABAC evaluation logic.
func tenantHasToolExecute(scopes []string, roles []string, toolRisk string) bool {
	// SuperAdmin / tenant_admin bypass
	for _, r := range roles {
		if r == RoleSuperAdmin || r == RoleTenantAdmin || r == RoleAgentOperator {
			return true
		}
	}

	// Check scopes
	hasExecute := false
	hasApprove := false
	for _, s := range scopes {
		if s == ScopeAll || s == ScopeToolExecute {
			hasExecute = true
		}
		if s == ScopeToolApprove {
			hasApprove = true
		}
	}

	if !hasExecute {
		return false
	}

	// Critical tools require tool:approve
	if toolRisk == RiskCritical {
		return hasApprove
	}

	return true
}

// AllRoles returns the list of all built-in role names.
// Used by the admin API to expose available roles.
func AllRoles() []string {
	return []string{
		RoleSuperAdmin,
		RoleTenantAdmin,
		RoleAgentOperator,
		RoleMember,
		RoleViewer,
	}
}

// AllScopes returns the list of all built-in scope strings.
// Used by the admin API to expose available scopes.
func AllScopes() []string {
	return []string{
		ScopeSessionRead,
		ScopeSessionWrite,
		ScopeSessionCreate,
		ScopeSessionDelete,
		ScopeAgentDispatch,
		ScopeAgentRead,
		ScopeToolExecute,
		ScopeToolApprove,
		ScopeMemoryRead,
		ScopeMemoryWrite,
		ScopeDocUpload,
		ScopeDocRead,
		ScopeAuditRead,
		ScopeTenantManage,
		ScopeRoleManage,
		ScopeBillingRead,
		ScopeWorkspaceAdmin,
		ScopeWorkspaceRead,
		ScopeModelManage,
	}
}
