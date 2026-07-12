package iam

import (
	"context"
	"testing"
	"time"
)

func TestTokenIssueVerify(t *testing.T) {
	issuer := NewTokenIssuer([]byte("test-secret-32bytes-xxxxxxxxxxxx"), "iam-service", time.Hour)
	tok, err := issuer.Issue(Claims{TenantID: "t1", UserID: "u1", Roles: []string{RoleMember}, Scopes: []string{ScopeSessionWrite}})
	if err != nil {
		t.Fatalf("issue: %v", err)
	}
	claims, err := issuer.Verify(tok)
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if claims.TenantID != "t1" || claims.UserID != "u1" {
		t.Fatalf("claims mismatch: %+v", claims)
	}
	if len(claims.Roles) != 1 || claims.Roles[0] != RoleMember {
		t.Fatalf("roles mismatch: %v", claims.Roles)
	}
}

func TestTokenDevMode(t *testing.T) {
	issuer := NewTokenIssuer(nil, "iam-service", time.Hour) // dev mode
	if !issuer.IsDevMode() {
		t.Fatal("expected dev mode")
	}
	// Dev mode returns empty claims regardless of token.
	claims, err := issuer.Verify("")
	if err != nil {
		t.Fatalf("verify in dev mode: %v", err)
	}
	if claims.TenantID != "" {
		t.Fatalf("dev mode should return empty claims, got %+v", claims)
	}
}

func TestTokenRejectBadSecret(t *testing.T) {
	issuer := NewTokenIssuer([]byte("real-secret-32bytes-xxxxxxxxxxxxx"), "iam-service", time.Hour)
	other := NewTokenIssuer([]byte("different-secret-32bytes-yyyyyyy"), "iam-service", time.Hour)
	tok, _ := issuer.Issue(Claims{TenantID: "t1", UserID: "u1"})
	if _, err := other.Verify(tok); err == nil {
		t.Fatal("verify with wrong secret should fail")
	}
}

func TestTenantContextHasScope(t *testing.T) {
	tc := TenantContext{TenantID: "t1", Scopes: []string{ScopeSessionWrite, ScopeToolExecute}}
	if !tc.HasScope(ScopeSessionWrite) {
		t.Fatal("should have session:write")
	}
	if tc.HasScope(ScopeTenantManage) {
		t.Fatal("should not have tenant:manage")
	}
	// SuperAdmin role bypasses scope check.
	tc2 := TenantContext{TenantID: "t1", Roles: []string{RoleSuperAdmin}}
	if !tc2.HasScope(ScopeTenantManage) {
		t.Fatal("super_admin should have all scopes")
	}
	// Dev mode with no scopes passes everything.
	tc3 := TenantContext{DevMode: true}
	if !tc3.HasScope(ScopeTenantManage) {
		t.Fatal("dev mode should pass scope checks")
	}
}

func TestPolicyHasScope(t *testing.T) {
	p := DefaultPolicy()
	if !p.HasScope([]string{RoleMember}, ScopeSessionWrite) {
		t.Fatal("member should have session:write")
	}
	if p.HasScope([]string{RoleViewer}, ScopeSessionWrite) {
		t.Fatal("viewer should not have session:write")
	}
	if !p.HasScope([]string{RoleTenantAdmin}, ScopeSessionDelete) {
		t.Fatal("tenant_admin should have session:delete")
	}
	// ScopeAll bypasses.
	if !p.HasScope([]string{RoleSuperAdmin}, ScopeBillingRead) {
		t.Fatal("super_admin (ScopeAll) should pass any scope")
	}
}

func TestScopesForRoles(t *testing.T) {
	scopes := ScopesForRoles([]string{RoleMember, RoleViewer})
	found := map[string]bool{}
	for _, s := range scopes {
		found[s] = true
	}
	if !found[ScopeSessionWrite] {
		t.Fatal("member+viewer should include session:write")
	}
	if !found[ScopeAuditRead] {
		t.Fatal("viewer adds audit:read")
	}
}

func TestEvaluateCrossTenantDeny(t *testing.T) {
	req := AuthzRequest{
		Principal: TenantContext{TenantID: "t1", Roles: []string{RoleMember}},
		Action:    ActionRead,
		Resource:  Resource{Type: "session", TenantID: "t2"},
	}
	if got := Evaluate(req); got != DecisionDeny {
		t.Fatalf("cross-tenant access should deny, got %s", got)
	}
}

func TestEvaluateSuperAdminAllow(t *testing.T) {
	req := AuthzRequest{
		Principal: TenantContext{TenantID: "t1", Roles: []string{RoleSuperAdmin}},
		Action:    ActionAdmin,
		Resource:  Resource{Type: "session", TenantID: "t2"},
	}
	if got := Evaluate(req); got != DecisionAllow {
		t.Fatalf("super_admin cross-tenant should allow, got %s", got)
	}
}

func TestEvaluateSensitiveTool(t *testing.T) {
	// High-risk tool → need_confirmation for a member.
	req := AuthzRequest{
		Principal: TenantContext{TenantID: "t1", Roles: []string{RoleMember}},
		Action:    ActionExecute,
		Resource:  Resource{Type: "tool", TenantID: "t1"},
		ToolRisk:  RiskHigh,
	}
	if got := Evaluate(req); got != DecisionNeedConfirmation {
		t.Fatalf("high-risk tool for member should need confirmation, got %s", got)
	}
	// Critical-risk tool without tool:approve scope → deny.
	req2 := req
	req2.ToolRisk = RiskCritical
	if got := Evaluate(req2); got != DecisionDeny {
		t.Fatalf("critical tool without approve scope should deny, got %s", got)
	}
	// Critical-risk tool with tool:approve scope → need_confirmation.
	req3 := req
	req3.ToolRisk = RiskCritical
	req3.Principal.Scopes = []string{ScopeToolApprove}
	if got := Evaluate(req3); got != DecisionNeedConfirmation {
		t.Fatalf("critical tool with approve scope should need confirmation, got %s", got)
	}
}

func TestBuiltinToolRisk(t *testing.T) {
	cases := []struct {
		tool     string
		wantRisk string
		wantConf bool
	}{
		{"rm -rf /", RiskCritical, true},
		{"docker run nginx", RiskHigh, true},
		{"kubectl delete pod", RiskHigh, true},
		{"git push --force origin main", RiskCritical, true},
		{"echo hello", RiskNormal, false},
		{"ls -la", RiskNormal, false},
	}
	for _, c := range cases {
		risk, conf := BuiltinToolRisk(c.tool)
		if risk != c.wantRisk || conf != c.wantConf {
			t.Errorf("BuiltinToolRisk(%q) = (%q,%v), want (%q,%v)", c.tool, risk, conf, c.wantRisk, c.wantConf)
		}
	}
}

func TestClassifyToolTenantOverride(t *testing.T) {
	// Tenant-specific rule overrides builtin.
	rules := []SensitiveToolRule{
		{TenantID: "t1", ToolName: "echo", RiskLevel: RiskHigh, RequiresConfirmation: true},
	}
	risk, conf := ClassifyTool("t1", "echo", rules)
	if risk != RiskHigh || !conf {
		t.Fatalf("tenant override should apply, got (%q,%v)", risk, conf)
	}
	// Other tenant falls back to builtin.
	risk2, conf2 := ClassifyTool("t2", "echo", rules)
	if risk2 != RiskNormal || conf2 {
		t.Fatalf("no override should fall back to builtin, got (%q,%v)", risk2, conf2)
	}
}

func TestCanConfirm(t *testing.T) {
	// Member can confirm high-risk tool.
	member := TenantContext{TenantID: "t1", Roles: []string{RoleMember}}
	if !CanConfirm(member, RiskHigh, SensitiveToolRule{}) {
		t.Fatal("member should confirm high risk")
	}
	// Member cannot confirm critical tool without approve scope or allowed role.
	if CanConfirm(member, RiskCritical, SensitiveToolRule{}) {
		t.Fatal("member should not confirm critical without approve scope")
	}
	// Member with allowed role can confirm critical.
	memberWithRole := TenantContext{TenantID: "t1", Roles: []string{RoleMember, "oncall"}}
	if !CanConfirm(memberWithRole, RiskCritical, SensitiveToolRule{AllowedRoles: []string{"oncall"}}) {
		t.Fatal("member with oncall role should confirm critical")
	}
	// Tenant admin can confirm anything.
	admin := TenantContext{TenantID: "t1", TenantRole: RoleTenantAdmin}
	if !CanConfirm(admin, RiskCritical, SensitiveToolRule{}) {
		t.Fatal("tenant_admin should confirm critical")
	}
}

func TestEnforceTenantScope(t *testing.T) {
	tc := TenantContext{TenantID: "t1", Roles: []string{RoleMember}}
	ctx := WithTenantContext(context.Background(), tc)
	if !EnforceTenantScope(ctx, "t1") {
		t.Fatal("same tenant should be allowed")
	}
	if EnforceTenantScope(ctx, "t2") {
		t.Fatal("cross-tenant should be denied")
	}
	// SuperAdmin bypasses.
	su := WithTenantContext(context.Background(), TenantContext{TenantID: "t1", Roles: []string{RoleSuperAdmin}})
	if !EnforceTenantScope(su, "t2") {
		t.Fatal("super_admin cross-tenant should be allowed")
	}
}
