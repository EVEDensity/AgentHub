package iam

import "testing"

// ═══════════════════════════════════════════════════════════════════════
//  RBAC Enhancement Tests (P0.3 — Sprint 4)
// ═══════════════════════════════════════════════════════════════════════
//  Tests the new agent_operator role, workspace/model scopes, and the
//  WorkspacePolicy type for per-workspace access control.
// ═══════════════════════════════════════════════════════════════════════

func TestAgentOperatorRoleExists(t *testing.T) {
	scopes, ok := DefaultRoleScopes[RoleAgentOperator]
	if !ok {
		t.Fatal("agent_operator role should exist in DefaultRoleScopes")
	}
	if len(scopes) == 0 {
		t.Fatal("agent_operator should have non-empty scopes")
	}
}

func TestAgentOperatorHasToolApprove(t *testing.T) {
	p := DefaultPolicy()
	// agent_operator can execute AND approve tools (including high-risk)
	if !p.HasScope([]string{RoleAgentOperator}, ScopeToolExecute) {
		t.Fatal("agent_operator should have tool:execute")
	}
	if !p.HasScope([]string{RoleAgentOperator}, ScopeToolApprove) {
		t.Fatal("agent_operator should have tool:approve")
	}
}

func TestAgentOperatorCannotManageTenant(t *testing.T) {
	p := DefaultPolicy()
	if p.HasScope([]string{RoleAgentOperator}, ScopeTenantManage) {
		t.Fatal("agent_operator should NOT have tenant:manage")
	}
	if p.HasScope([]string{RoleAgentOperator}, ScopeRoleManage) {
		t.Fatal("agent_operator should NOT have role:manage")
	}
	if p.HasScope([]string{RoleAgentOperator}, ScopeBillingRead) {
		t.Fatal("agent_operator should NOT have billing:read")
	}
}

func TestAgentOperatorCanManageWorkspace(t *testing.T) {
	p := DefaultPolicy()
	if !p.HasScope([]string{RoleAgentOperator}, ScopeWorkspaceAdmin) {
		t.Fatal("agent_operator should have workspace:admin")
	}
	if !p.HasScope([]string{RoleAgentOperator}, ScopeWorkspaceRead) {
		t.Fatal("agent_operator should have workspace:read")
	}
	if !p.HasScope([]string{RoleAgentOperator}, ScopeModelManage) {
		t.Fatal("agent_operator should have model:manage")
	}
}

func TestMemberHasWorkspaceRead(t *testing.T) {
	p := DefaultPolicy()
	if !p.HasScope([]string{RoleMember}, ScopeWorkspaceRead) {
		t.Fatal("member should have workspace:read (P0.3 baseline)")
	}
	// Member should NOT have workspace:admin
	if p.HasScope([]string{RoleMember}, ScopeWorkspaceAdmin) {
		t.Fatal("member should NOT have workspace:admin")
	}
}

func TestAllRolesReturnsFive(t *testing.T) {
	roles := AllRoles()
	if len(roles) != 5 {
		t.Fatalf("AllRoles should return 5 roles, got %d: %v", len(roles), roles)
	}
	// Verify agent_operator is included
	found := false
	for _, r := range roles {
		if r == RoleAgentOperator {
			found = true
			break
		}
	}
	if !found {
		t.Fatal("AllRoles should include agent_operator")
	}
}

func TestAllScopesIncludesNewScopes(t *testing.T) {
	scopes := AllScopes()
	wantScopes := []string{ScopeWorkspaceAdmin, ScopeWorkspaceRead, ScopeModelManage, ScopeMissionClaim}
	for _, want := range wantScopes {
		found := false
		for _, s := range scopes {
			if s == want {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("AllScopes should include %s", want)
		}
	}
}

func TestMissionClaimRequiresExplicitWorkspaceGrant(t *testing.T) {
	p := DefaultPolicy()
	for _, role := range []string{RoleTenantAdmin, RoleAgentOperator, RoleMember, RoleViewer} {
		if p.HasScope([]string{role}, ScopeMissionClaim) {
			t.Fatalf("%s should not receive mission:claim by default", role)
		}
	}
}

func TestScopesForRolesAgentOperator(t *testing.T) {
	scopes := ScopesForRoles([]string{RoleAgentOperator})
	found := map[string]bool{}
	for _, s := range scopes {
		found[s] = true
	}
	// Should have tool:approve and workspace:admin
	if !found[ScopeToolApprove] {
		t.Fatal("agent_operator scopes should include tool:approve")
	}
	if !found[ScopeWorkspaceAdmin] {
		t.Fatal("agent_operator scopes should include workspace:admin")
	}
	// Should NOT have tenant:manage
	if found[ScopeTenantManage] {
		t.Fatal("agent_operator scopes should NOT include tenant:manage")
	}
}

// ── WorkspacePolicy Tests ──────────────────────────────────────────────

func TestWorkspacePolicySetGet(t *testing.T) {
	p := NewWorkspacePolicy()
	acl := WorkspaceACL{
		WorkspaceID: "ws-1",
		UserID:      "user-1",
		Role:        RoleAgentOperator,
		Permissions: []string{ScopeToolExecute, ScopeToolApprove},
	}
	p.SetACL(acl)

	got, ok := p.GetACL("ws-1", "user-1")
	if !ok {
		t.Fatal("ACL should exist after SetACL")
	}
	if got.Role != RoleAgentOperator {
		t.Fatalf("role mismatch: got %s, want %s", got.Role, RoleAgentOperator)
	}
	if len(got.Permissions) != 2 {
		t.Fatalf("permissions count: got %d, want 2", len(got.Permissions))
	}
}

func TestWorkspacePolicyRemove(t *testing.T) {
	p := NewWorkspacePolicy()
	p.SetACL(WorkspaceACL{WorkspaceID: "ws-1", UserID: "user-1", Role: RoleMember})

	if !p.RemoveACL("ws-1", "user-1") {
		t.Fatal("RemoveACL should return true for existing ACL")
	}
	if _, ok := p.GetACL("ws-1", "user-1"); ok {
		t.Fatal("ACL should not exist after RemoveACL")
	}
	if p.RemoveACL("ws-1", "user-1") {
		t.Fatal("RemoveACL should return false for non-existent ACL")
	}
}

func TestWorkspacePolicyList(t *testing.T) {
	p := NewWorkspacePolicy()
	p.SetACL(WorkspaceACL{WorkspaceID: "ws-1", UserID: "user-1", Role: RoleMember})
	p.SetACL(WorkspaceACL{WorkspaceID: "ws-1", UserID: "user-2", Role: RoleViewer})

	list := p.ListACL("ws-1")
	if len(list) != 2 {
		t.Fatalf("ListACL should return 2 entries, got %d", len(list))
	}

	// Non-existent workspace
	if list := p.ListACL("ws-nonexistent"); list != nil {
		t.Fatalf("ListACL for non-existent workspace should return nil, got %v", list)
	}
}

func TestWorkspacePolicyCanExecuteWithWorkspaceAdmin(t *testing.T) {
	p := NewWorkspacePolicy()
	// User with workspace:admin scope at tenant level → can execute anything
	canExec := p.CanExecute("ws-1", "user-1", RiskCritical,
		[]string{ScopeWorkspaceAdmin}, []string{RoleMember})
	if !canExec {
		t.Fatal("workspace:admin scope should allow execution of any risk")
	}
}

func TestWorkspacePolicyCanExecuteWithACL(t *testing.T) {
	p := NewWorkspacePolicy()
	p.SetACL(WorkspaceACL{
		WorkspaceID: "ws-1",
		UserID:      "user-1",
		Role:        RoleAgentOperator,
	})

	// agent_operator in workspace → can execute high risk
	canExec := p.CanExecute("ws-1", "user-1", RiskHigh,
		[]string{ScopeToolExecute}, []string{RoleMember})
	if !canExec {
		t.Fatal("agent_operator workspace ACL should allow high-risk execution")
	}
}

func TestWorkspacePolicyCanExecuteFallbackToTenant(t *testing.T) {
	p := NewWorkspacePolicy()
	// No workspace ACL → fall back to tenant RBAC
	// Member with tool:execute can run normal risk
	canExec := p.CanExecute("ws-1", "user-1", RiskNormal,
		[]string{ScopeToolExecute}, []string{RoleMember})
	if !canExec {
		t.Fatal("member with tool:execute should be able to execute normal-risk tools")
	}

	// Member without tool:approve cannot execute critical
	canExec = p.CanExecute("ws-1", "user-1", RiskCritical,
		[]string{ScopeToolExecute}, []string{RoleMember})
	if canExec {
		t.Fatal("member without tool:approve should NOT execute critical tools")
	}
}

func TestWorkspacePolicyCanExecuteViewerDenied(t *testing.T) {
	p := NewWorkspacePolicy()
	p.SetACL(WorkspaceACL{
		WorkspaceID: "ws-1",
		UserID:      "user-1",
		Role:        RoleViewer,
	})

	// Viewer in workspace → cannot execute
	canExec := p.CanExecute("ws-1", "user-1", RiskNormal,
		[]string{}, []string{RoleMember})
	if canExec {
		t.Fatal("viewer workspace ACL should deny execution")
	}
}

func TestWorkspacePolicyCanExecuteWithPermissions(t *testing.T) {
	p := NewWorkspacePolicy()
	p.SetACL(WorkspaceACL{
		WorkspaceID: "ws-1",
		UserID:      "user-1",
		Role:        RoleViewer,                 // viewer normally can't execute
		Permissions: []string{ScopeToolExecute}, // but has explicit tool:execute
	})

	canExec := p.CanExecute("ws-1", "user-1", RiskNormal,
		[]string{}, []string{RoleMember})
	if !canExec {
		t.Fatal("viewer with explicit tool:execute permission should be able to execute")
	}
}

// ── Backward Compatibility Tests ───────────────────────────────────────

func TestExistingRolesStillWork(t *testing.T) {
	p := DefaultPolicy()
	// super_admin should still bypass everything
	if !p.HasScope([]string{RoleSuperAdmin}, ScopeBillingRead) {
		t.Fatal("super_admin should still have billing:read")
	}
	// tenant_admin should still have tenant:manage
	if !p.HasScope([]string{RoleTenantAdmin}, ScopeTenantManage) {
		t.Fatal("tenant_admin should still have tenant:manage")
	}
	// member should still NOT have session:delete
	if p.HasScope([]string{RoleMember}, ScopeSessionDelete) {
		t.Fatal("member should still NOT have session:delete")
	}
	// viewer should still have audit:read
	if !p.HasScope([]string{RoleViewer}, ScopeAuditRead) {
		t.Fatal("viewer should still have audit:read")
	}
}
