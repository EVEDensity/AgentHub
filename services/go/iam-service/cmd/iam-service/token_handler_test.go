package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/agenthub/platform/shared/iam"
)

func TestTokenIssuanceRequiresOperatorAndAssignedPrincipal(t *testing.T) {
	key := []byte(strings.Repeat("j", 40))
	operator := []byte(strings.Repeat("o", 40))
	issuer := iam.NewTokenIssuer(key, "iam-service", time.Hour)
	lookups := 0
	load := func(_ context.Context, tenant, user string) ([]string, []string, string, error) {
		lookups++
		if tenant != "tenant-1" || user != "user-1" {
			return nil, nil, "", errors.New("not assigned")
		}
		return []string{"viewer"}, []string{"session:read"}, "viewer", nil
	}
	handler := serviceAuthorization(operator, issueTokenHandler(issuer, time.Hour, load))
	for _, tc := range []struct {
		auth, body string
		want       int
	}{
		{"", `{"tenant_id":"tenant-1","user_id":"user-1"}`, 401},
		{"Bearer ordinary-user-token", `{"tenant_id":"tenant-1","user_id":"user-1"}`, 401},
		{"Bearer " + string(operator), `{"tenant_id":"other","user_id":"admin"}`, 403},
		{"Bearer " + string(operator), `{"tenant_id":"tenant-1","user_id":"user-1","roles":["super_admin"]}`, 400},
		{"Bearer " + string(operator), `{"tenant_id":"tenant-1","user_id":"user-1"} {}`, 400},
		{"Bearer " + string(operator), `{"tenant_id":"tenant-1","user_id":"user-1"}`, 200},
	} {
		req := httptest.NewRequest(http.MethodPost, "/iam/token", strings.NewReader(tc.body))
		req.Header.Set("Authorization", tc.auth)
		out := httptest.NewRecorder()
		before := lookups
		handler.ServeHTTP(out, req)
		if out.Code != tc.want {
			t.Fatalf("got=%d want=%d body=%s", out.Code, tc.want, out.Body.String())
		}
		if tc.want == 401 && lookups != before {
			t.Fatal("unauthorized caller reached principal storage")
		}
		if tc.want == 200 {
			var result TokenResponse
			if err := json.Unmarshal(out.Body.Bytes(), &result); err != nil {
				t.Fatal(err)
			}
			claims, err := issuer.Verify(result.AccessToken)
			if err != nil || claims.TenantID != "tenant-1" || claims.UserID != "user-1" || len(claims.Scopes) != 1 || claims.Scopes[0] != "session:read" {
				t.Fatalf("issued incorrect identity: %v", err)
			}
		}
	}
}

func TestTokenIssuerFailsClosedOnUnavailablePrincipalOrSigning(t *testing.T) {
	for _, dev := range []bool{false, true} {
		key := []byte(strings.Repeat("j", 40))
		want := http.StatusForbidden
		if dev {
			key = nil
			want = http.StatusServiceUnavailable
		}
		issuer := iam.NewTokenIssuer(key, "iam-service", time.Hour)
		load := func(context.Context, string, string) ([]string, []string, string, error) {
			return nil, nil, "", errors.New("storage unavailable")
		}
		handler := issueTokenHandler(issuer, time.Hour, load)
		req := httptest.NewRequest(http.MethodPost, "/iam/token", strings.NewReader(`{"tenant_id":"tenant-1","user_id":"user-1"}`))
		out := httptest.NewRecorder()
		handler.ServeHTTP(out, req)
		if out.Code != want || strings.Contains(out.Body.String(), "access_token") {
			t.Fatalf("dev=%v got=%d body=%s", dev, out.Code, out.Body.String())
		}
	}
}
