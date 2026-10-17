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

type principalScopeLoader func(context.Context, string, string) ([]string, []string, string, error)

func issueTokenHandler(issuer *iam.TokenIssuer, ttl time.Duration, load principalScopeLoader) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if issuer.IsDevMode() {
			jsonError(w, http.StatusServiceUnavailable, "JWT signing is not configured")
			return
		}
		var req TokenRequest
		decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16<<10))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&req); err != nil {
			jsonError(w, http.StatusBadRequest, "invalid token request")
			return
		}
		if err := decoder.Decode(new(any)); err != io.EOF {
			jsonError(w, http.StatusBadRequest, "one token request is required")
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
			jsonError(w, http.StatusForbidden, "principal unavailable or without assigned roles")
			return
		}
		token, err := issuer.Issue(iam.Claims{
			TenantID: req.TenantID, UserID: req.UserID,
			Roles: roles, Scopes: scopes, TenantRole: tenantRole,
		})
		if err != nil {
			jsonError(w, http.StatusInternalServerError, "token issuance failed")
			return
		}
		tokensIssued.WithLabelValues(req.TenantID, tenantRole).Inc()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(TokenResponse{
			AccessToken: token, TokenType: "Bearer", ExpiresAt: time.Now().UTC().Add(ttl),
			TenantID: req.TenantID, UserID: req.UserID, Roles: roles, Scopes: scopes,
		})
	})
}
