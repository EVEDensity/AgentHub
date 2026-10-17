package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestManagementRoutesRequireDedicatedServiceIdentity(t *testing.T) {
	token := strings.Repeat("operator-", 5)
	for _, route := range []string{"/iam/token", "/iam/tenants", "/iam/roles", "/iam/quotas", "/iam/secrets/reveal", "/iam/secrets/rotate"} {
		for _, credential := range []string{"", "Bearer user-jwt", "Basic " + token} {
			t.Run(route+credential, func(t *testing.T) {
				called := false
				handler := serviceAuthorization([]byte(token), http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					called = true
				}))
				req := httptest.NewRequest(http.MethodPost, route+"?token="+token, strings.NewReader(`{"tenant_id":"other","user_id":"admin"}`))
				req.Header.Set("Authorization", credential)
				out := httptest.NewRecorder()
				handler.ServeHTTP(out, req)
				if out.Code != http.StatusUnauthorized || called {
					t.Fatalf("untrusted caller reached %s: status=%d called=%v", route, out.Code, called)
				}
				if strings.Contains(out.Body.String(), token) {
					t.Fatal("credential leaked in response")
				}
			})
		}
	}
}

func TestServiceAuthorizationAcceptsOnlyOneHeader(t *testing.T) {
	token := strings.Repeat("operator-", 5)
	handler := serviceAuthorization([]byte(token), http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	for _, duplicate := range []bool{false, true} {
		req := httptest.NewRequest(http.MethodPost, "/iam/token", nil)
		req.Header.Set("Authorization", "Bearer "+token)
		want := http.StatusNoContent
		if duplicate {
			req.Header.Add("Authorization", "Bearer "+token)
			want = http.StatusUnauthorized
		}
		out := httptest.NewRecorder()
		handler.ServeHTTP(out, req)
		if out.Code != want {
			t.Fatalf("duplicate=%v got=%d want=%d", duplicate, out.Code, want)
		}
	}
}

func TestUnconfiguredServiceCannotServeManagement(t *testing.T) {
	handler := serviceAuthorization(nil, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	for _, route := range []string{"/iam/token", "/healthz", "/metrics", "/profile"} {
		out := httptest.NewRecorder()
		handler.ServeHTTP(out, httptest.NewRequest(http.MethodGet, route, nil))
		want := http.StatusServiceUnavailable
		if route != "/iam/token" {
			want = http.StatusNoContent
		}
		if out.Code != want {
			t.Fatalf("%s got=%d want=%d", route, out.Code, want)
		}
	}
}

func TestServiceCredentialsRejectMissingWeakAndSharedSecrets(t *testing.T) {
	for _, tc := range []struct{ name, jwtKey, token string }{
		{"missing JWT", "", strings.Repeat("x", 40)},
		{"weak JWT", "short", strings.Repeat("x", 40)},
		{"weak service token", strings.Repeat("j", 40), "short"},
		{"multiline token", strings.Repeat("j", 40), strings.Repeat("x", 32) + "\nsecond"},
		{"same secret", strings.Repeat("x", 40), strings.Repeat("x", 40)},
		{"oversized token", strings.Repeat("j", 40), strings.Repeat("x", 4097)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			file := filepath.Join(t.TempDir(), "operator-token")
			if err := os.WriteFile(file, []byte(tc.token), 0600); err != nil {
				t.Fatal(err)
			}
			getenv := func(name string) string {
				if name == "JWT_SECRET" {
					return tc.jwtKey
				}
				return file
			}
			if _, _, err := loadServiceCredentials(getenv); err == nil {
				t.Fatal("unsafe credentials were accepted")
			}
		})
	}
	for _, file := range []string{"", t.TempDir(), filepath.Join(t.TempDir(), "absent")} {
		getenv := func(name string) string {
			if name == "JWT_SECRET" {
				return strings.Repeat("j", 40)
			}
			return file
		}
		if _, _, err := loadServiceCredentials(getenv); err == nil {
			t.Fatal("missing/non-file token accepted")
		}
	}
}

func TestServiceCredentialFileAllowsOneTrailingNewline(t *testing.T) {
	file := filepath.Join(t.TempDir(), "operator-token")
	token := strings.Repeat("o", 40)
	if err := os.WriteFile(file, []byte(token+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	key, loaded, err := loadServiceCredentials(func(name string) string {
		if name == "JWT_SECRET" {
			return strings.Repeat("j", 40)
		}
		return file
	})
	if err != nil || len(key) != 40 || string(loaded) != token {
		t.Fatalf("valid mounted credentials rejected: %v", err)
	}
}
