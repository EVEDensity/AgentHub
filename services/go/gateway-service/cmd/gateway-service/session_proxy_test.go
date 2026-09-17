package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/agenthub/platform/shared/iam"
)

func TestSessionProxyUsesAuthenticatedTenantAndBoundedLimit(t *testing.T) {
	var tenantID, limit, authorization string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tenantID = r.URL.Query().Get("tenant_id")
		limit = r.URL.Query().Get("limit")
		authorization = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"sessions":[]}`))
	}))
	defer upstream.Close()

	request := httptest.NewRequest(http.MethodGet, "/platform/sessions?tenant_id=tenant-attacker&limit=500", nil)
	request.Header.Set("Authorization", "Bearer verified-token")
	request = request.WithContext(iam.WithTenantContext(request.Context(), iam.TenantContext{
		TenantID: "tenant-real",
		UserID:   "actor-real",
	}))
	recorder := httptest.NewRecorder()
	newSessionProxy(upstream.URL).ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", recorder.Code)
	}
	if tenantID != "tenant-real" {
		t.Fatalf("tenant_id = %q, want authenticated tenant", tenantID)
	}
	if limit != "50" {
		t.Fatalf("limit = %q, want 50", limit)
	}
	if authorization != "Bearer verified-token" {
		t.Fatalf("authorization = %q, want forwarded credential", authorization)
	}
}

func TestSessionProxyFailsBeforeNetworkWithoutTenantContext(t *testing.T) {
	called := false
	upstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		called = true
	}))
	defer upstream.Close()

	recorder := httptest.NewRecorder()
	newSessionProxy(upstream.URL).ServeHTTP(
		recorder,
		httptest.NewRequest(http.MethodGet, "/platform/sessions?tenant_id=tenant-attacker", nil),
	)
	if recorder.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want 403", recorder.Code)
	}
	if called {
		t.Fatal("session service must not be called without authenticated tenant context")
	}
}

func TestSessionProxyRejectsUnsupportedMethod(t *testing.T) {
	request := httptest.NewRequest(http.MethodPost, "/platform/sessions", nil)
	request = request.WithContext(iam.WithTenantContext(context.Background(), iam.TenantContext{
		TenantID: "tenant-real",
		UserID:   "actor-real",
	}))
	recorder := httptest.NewRecorder()
	newSessionProxy("http://session.test").ServeHTTP(recorder, request)
	if recorder.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", recorder.Code)
	}
}

func TestNormalizeSessionLimit(t *testing.T) {
	tests := map[string]int{
		"":    10,
		"bad": 10,
		"0":   10,
		"-5":  10,
		"1":   1,
		"50":  50,
		"51":  50,
	}
	for raw, want := range tests {
		if got := normalizeSessionLimit(raw); got != want {
			t.Fatalf("normalizeSessionLimit(%q) = %d, want %d", raw, got, want)
		}
	}
}
