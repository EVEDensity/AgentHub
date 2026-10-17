package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/agenthub/platform/shared/iam"
)

func authorizeHandler(load principalScopeLoader) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var req AuthzRequest
		decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16<<10))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&req); err != nil {
			jsonError(w, http.StatusBadRequest, "invalid authorization request")
			return
		}
		if err := decoder.Decode(new(any)); err != io.EOF {
			jsonError(w, http.StatusBadRequest, "one authorization request is required")
			return
		}
		if req.TenantID == "" || req.UserID == "" || strings.TrimSpace(req.TenantID) != req.TenantID || strings.TrimSpace(req.UserID) != req.UserID {
			jsonError(w, http.StatusBadRequest, "tenant_id and user_id are required")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		roles, scopes, tenantRole, err := load(ctx, req.TenantID, req.UserID)
		if err != nil || len(roles) == 0 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusForbidden)
			_ = json.NewEncoder(w).Encode(AuthzResponse{Decision: string(iam.DecisionDeny), Reason: "principal unavailable or without assigned roles"})
			return
		}
		decision := iam.Evaluate(iam.AuthzRequest{
			Principal: iam.TenantContext{TenantID: req.TenantID, UserID: req.UserID, Roles: roles, Scopes: scopes, TenantRole: tenantRole},
			Action:    iam.Action(req.Action),
			Resource:  iam.Resource{Type: req.Resource.Type, TenantID: req.Resource.TenantID, OwnerID: req.Resource.OwnerID, Visibility: req.Resource.Visibility},
			ToolRisk:  req.ToolRisk,
		})
		reason := ""
		switch decision {
		case iam.DecisionDeny:
			reason = "cross-tenant access denied or insufficient role for critical tool"
		case iam.DecisionNeedConfirmation:
			reason = "sensitive tool requires explicit confirmation"
		}
		authzDecisions.WithLabelValues(string(decision), req.Action, req.Resource.Type).Inc()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(AuthzResponse{Decision: string(decision), Reason: reason, TenantRole: tenantRole})
	})
}
