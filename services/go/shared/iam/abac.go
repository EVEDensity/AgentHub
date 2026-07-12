package iam

import "strings"

// ── Risk levels ────────────────────────────────────────────────────────
// Risk classification for tools and actions. Sensitive tools (high/critical)
// require explicit user confirmation via the tool-permission-service flow;
// critical tools additionally restrict the set of roles allowed to confirm.

const (
	RiskLow      = "low"
	RiskNormal   = "normal"
	RiskHigh     = "high"
	RiskCritical = "critical"
)

// Action is the verb an authorization request is about.
type Action string

const (
	ActionRead    Action = "read"
	ActionWrite   Action = "write"
	ActionExecute Action = "execute"
	ActionAdmin   Action = "admin"
	ActionApprove Action = "approve"
)

// Resource is the object being acted upon. TenantID scopes the resource to a
// tenant; OwnerID enables ownership checks (a member can write their own
// sessions but not another tenant's); Visibility encodes sharing (private/
// tenant/public).
type Resource struct {
	Type       string // "session" | "tool" | "agent" | "document" | "memory" | "tenant"
	TenantID   string
	OwnerID    string
	Visibility string // "private" | "tenant" | "public"
}

// Decision is the outcome of an ABAC evaluation.
type Decision string

const (
	DecisionAllow            Decision = "allow"
	DecisionDeny             Decision = "deny"
	DecisionNeedConfirmation Decision = "need_confirmation"
)

// AuthzRequest bundles the inputs to an authorization decision: who (TenantContext),
// what (Action), on which (Resource), plus optional tool risk for the sensitive-tool
// confirmation path.
type AuthzRequest struct {
	Principal TenantContext
	Action    Action
	Resource  Resource
	// ToolRisk is the risk level when Resource.Type == "tool". Populated by
	// tool-permission-service after consulting platform_sensitive_tools.
	ToolRisk string
}

// Evaluate decides allow / deny / need_confirmation. The logic is:
//  1. Dev mode → allow (local development unblocked).
//  2. SuperAdmin / tenant_admin → allow (break-glass / tenant owner).
//  3. Cross-tenant access → deny (the core multi-tenant isolation rule).
//  4. Tool resource with high/critical risk → need_confirmation (unless the
//     principal carries tool:approve and the risk is high, not critical).
//  5. Otherwise defer to the scope check (caller does HasScope with the
//     action-appropriate scope).
func Evaluate(req AuthzRequest) Decision {
	if req.Principal.DevMode {
		return DecisionAllow
	}
	if req.Principal.HasRole(RoleSuperAdmin) || req.Principal.TenantRole == RoleTenantAdmin {
		return DecisionAllow
	}
	// Multi-tenant isolation: a principal may only touch resources in their
	// own tenant. The gateway already strips cross-tenant session ids, but
	// this is the defense-in-depth check every service applies.
	if req.Resource.TenantID != "" && req.Principal.TenantID != "" && req.Resource.TenantID != req.Principal.TenantID {
		return DecisionDeny
	}
	// Sensitive-tool confirmation: high risk needs one-click confirm, critical
	// risk additionally requires the tool:approve scope (so a plain member
	// cannot self-approve a destructive tool like rm -rf).
	if req.Resource.Type == "tool" {
		switch req.ToolRisk {
		case RiskCritical:
			if !req.Principal.HasScope(ScopeToolApprove) {
				return DecisionDeny
			}
			return DecisionNeedConfirmation
		case RiskHigh:
			return DecisionNeedConfirmation
		}
	}
	return DecisionAllow
}

// SensitiveToolRule is one row of platform_sensitive_tools: a tool classified
// by risk for a tenant, with the roles permitted to confirm it. An empty
// AllowedRoles list means "any authenticated principal in the tenant".
type SensitiveToolRule struct {
	TenantID            string
	ToolName            string
	RiskLevel           string
	RequiresConfirmation bool
	AllowedRoles        []string
}

// ClassifyTool looks up the risk level for a tool name. It first matches an
// exact tenant-specific rule, then falls back to the built-in pattern table
// (rm -rf, docker, kubectl, etc.). Returns (risk, requiresConfirmation).
func ClassifyTool(tenantID, toolName string, tenantRules []SensitiveToolRule) (string, bool) {
	for _, r := range tenantRules {
		if r.TenantID == tenantID && strings.EqualFold(r.ToolName, toolName) {
			return r.RiskLevel, r.RequiresConfirmation
		}
	}
	return BuiltinToolRisk(toolName)
}

// builtinSensitiveToolPattern is a risk-classified tool-name fragment. Longer
// fragments are checked first so that more-specific patterns (e.g.
// "git push --force") take priority over shorter ones (e.g. "git push").
type builtinSensitiveToolPattern struct {
	frag string
	risk string
}

// builtinSensitiveTools lists tool-name fragments in descending length order so
// that the most-specific match wins. Order matters — do not re-sort without
// understanding the matching logic in BuiltinToolRisk.
var builtinSensitiveTools = []builtinSensitiveToolPattern{
	// ── longer / compound patterns first ──
	{":(){ :|:& };", RiskCritical},
	{"git push --force", RiskCritical},
	// ── single-command / shorter patterns ──
	{"rm -rf", RiskCritical},
	{"rm -fr", RiskCritical},
	{"rmdir /", RiskCritical},
	{"mkfs", RiskCritical},
	{"dd if=", RiskCritical},
	{"shutdown", RiskCritical},
	{"reboot", RiskHigh},
	{"docker", RiskHigh},
	{"kubectl", RiskHigh},
	{"helm", RiskHigh},
	{"git push", RiskHigh},
	{"curl ", RiskNormal},
	{"wget ", RiskNormal},
	{"scp ", RiskNormal},
	{"chmod", RiskNormal},
	{"chown", RiskNormal},
}

// BuiltinToolRisk returns the built-in risk for a tool invocation. A tool that
// matches no pattern is "normal" and does not require confirmation.
func BuiltinToolRisk(toolName string) (string, bool) {
	lower := strings.ToLower(toolName)
	for _, p := range builtinSensitiveTools {
		if strings.Contains(lower, p.frag) {
			return p.risk, p.risk == RiskHigh || p.risk == RiskCritical
		}
	}
	return RiskNormal, false
}

// CanConfirm reports whether the principal is permitted to confirm a sensitive
// tool at the given risk level. Critical tools require an explicit allowed-role
// membership (or the tool:approve scope); high tools allow any tenant member.
func CanConfirm(principal TenantContext, risk string, rule SensitiveToolRule) bool {
	if principal.DevMode {
		return true
	}
	if principal.HasRole(RoleSuperAdmin) || principal.TenantRole == RoleTenantAdmin {
		return true
	}
	if risk == RiskCritical {
		if principal.HasScope(ScopeToolApprove) {
			return true
		}
		for _, r := range rule.AllowedRoles {
			if principal.HasRole(r) {
				return true
			}
		}
		return false
	}
	// high risk: any authenticated tenant member can confirm
	return principal.TenantID != ""
}
