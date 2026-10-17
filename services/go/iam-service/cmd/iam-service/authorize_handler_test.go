package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAuthorizationCannotAllowUnavailableOrUnassignedPrincipal(t *testing.T) {
	for _, unavailable := range []bool{false, true} {
		load := func(context.Context, string, string) ([]string, []string, string, error) {
			if unavailable {
				return []string{"tenant_admin"}, nil, "tenant_admin", errors.New("storage unavailable")
			}
			return nil, nil, "", nil
		}
		operator := []byte(strings.Repeat("o", 40))
		handler := serviceAuthorization(operator, authorizeHandler(load))
		req := httptest.NewRequest(http.MethodPost, "/iam/authorize", strings.NewReader(`{"tenant_id":"t","user_id":"u","action":"read","resource":{"type":"session","tenant_id":"t"}}`))
		req.Header.Set("Authorization", "Bearer "+string(operator))
		out := httptest.NewRecorder()
		handler.ServeHTTP(out, req)
		var result AuthzResponse
		if err := json.Unmarshal(out.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if out.Code != http.StatusForbidden || result.Decision != "deny" {
			t.Fatalf("unavailable=%v code=%d result=%+v", unavailable, out.Code, result)
		}
	}
}

func TestAuthorizationRetainsAssignedPrincipalAndTenantBoundary(t *testing.T) {
	load := func(context.Context, string, string) ([]string, []string, string, error) {
		return []string{"viewer"}, []string{"session:read"}, "viewer", nil
	}
	for _, tc := range []struct{ tenant, decision string }{{"t", "allow"}, {"other", "deny"}} {
		body := `{"tenant_id":"t","user_id":"u","action":"read","resource":{"type":"session","tenant_id":"` + tc.tenant + `"}}`
		out := httptest.NewRecorder()
		authorizeHandler(load).ServeHTTP(out, httptest.NewRequest(http.MethodPost, "/iam/authorize", strings.NewReader(body)))
		var result AuthzResponse
		if err := json.Unmarshal(out.Body.Bytes(), &result); err != nil || out.Code != 200 || result.Decision != tc.decision {
			t.Fatalf("tenant=%s code=%d body=%s", tc.tenant, out.Code, out.Body.String())
		}
	}
}

func TestAuthorizationRejectsInvalidIdentityBeforeStorage(t *testing.T) {
	load := func(context.Context, string, string) ([]string, []string, string, error) {
		t.Fatal("invalid request reached principal storage")
		return nil, nil, "", nil
	}
	for _, body := range []string{`{}`, `{"tenant_id":"t","user_id":" "}`, `{"tenant_id":"t","user_id":"u","roles":["admin"]}`, `{"tenant_id":"t","user_id":"u"} {}`} {
		out := httptest.NewRecorder()
		authorizeHandler(load).ServeHTTP(out, httptest.NewRequest(http.MethodPost, "/iam/authorize", strings.NewReader(body)))
		if out.Code != http.StatusBadRequest {
			t.Fatalf("code=%d body=%s", out.Code, out.Body.String())
		}
	}
}
