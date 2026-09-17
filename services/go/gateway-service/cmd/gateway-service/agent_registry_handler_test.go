package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/agenthub/platform/shared/iam"
)

func TestAgentRegistryHandlerUsesAuthenticatedActorAndTenantProjection(t *testing.T) {
	var gotUserID string
	handler := &agentRegistryHandler{
		list: func(_ context.Context, userID string) ([]agentCatalogEntry, error) {
			gotUserID = userID
			return []agentCatalogEntry{{
				AgentID:      "reviewer",
				DisplayName:  "Reviewer",
				Domain:       "review",
				Status:       "online",
				AdapterType:  "openai",
				ModelName:    "review-model",
				RiskLevel:    "L1",
				DutyNote:     "Reviews changes",
				Capabilities: []string{"code.review"},
			}}, nil
		},
	}
	request := httptest.NewRequest(http.MethodGet, "/platform/agent-registry?tenant_id=tenant-attacker", nil)
	request = request.WithContext(iam.WithTenantContext(request.Context(), iam.TenantContext{
		TenantID: "tenant-real",
		UserID:   "actor-real",
	}))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", recorder.Code)
	}
	if gotUserID != "actor-real" {
		t.Fatalf("catalog user_id = %q, want authenticated actor", gotUserID)
	}
	var payload struct {
		TenantID string              `json:"tenant_id"`
		Count    int                 `json:"count"`
		Agents   []agentCatalogEntry `json:"agents"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if payload.TenantID != "tenant-real" || payload.Count != 1 || len(payload.Agents) != 1 {
		t.Fatalf("unexpected projection: %+v", payload)
	}
}

func TestAgentRegistryHandlerFailsClosedWithoutIdentity(t *testing.T) {
	called := false
	handler := &agentRegistryHandler{
		list: func(context.Context, string) ([]agentCatalogEntry, error) {
			called = true
			return nil, nil
		},
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/platform/agent-registry", nil))
	if recorder.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want 403", recorder.Code)
	}
	if called {
		t.Fatal("catalog must not be queried without authenticated identity")
	}
}

func TestAgentRegistryRouteRequiresAgentReadScope(t *testing.T) {
	called := false
	handler := iam.RequireScope(iam.ScopeAgentRead)(&agentRegistryHandler{
		list: func(context.Context, string) ([]agentCatalogEntry, error) {
			called = true
			return nil, nil
		},
	})
	request := httptest.NewRequest(http.MethodGet, "/platform/agent-registry", nil)
	request = request.WithContext(iam.WithTenantContext(request.Context(), iam.TenantContext{
		TenantID: "tenant-real",
		UserID:   "actor-real",
		Scopes:   []string{iam.ScopeSessionRead},
	}))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want 403", recorder.Code)
	}
	if called {
		t.Fatal("catalog must not be queried without agent:read scope")
	}
}

func TestAgentRegistryHandlerReturnsUnavailableOnCatalogFailure(t *testing.T) {
	handler := &agentRegistryHandler{
		list: func(context.Context, string) ([]agentCatalogEntry, error) {
			return nil, errors.New("database failed")
		},
	}
	request := httptest.NewRequest(http.MethodGet, "/platform/agent-registry", nil)
	request = request.WithContext(iam.WithTenantContext(request.Context(), iam.TenantContext{
		TenantID: "tenant-real",
		UserID:   "actor-real",
	}))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want 502", recorder.Code)
	}
}

func TestAgentRegistryHandlerRejectsUnsupportedMethod(t *testing.T) {
	recorder := httptest.NewRecorder()
	(&agentRegistryHandler{}).ServeHTTP(
		recorder,
		httptest.NewRequest(http.MethodPost, "/platform/agent-registry", nil),
	)
	if recorder.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", recorder.Code)
	}
}

func TestParseCapabilityTags(t *testing.T) {
	if got := parseCapabilityTags(`["code.review","test.run"]`); len(got) != 2 || got[0] != "code.review" {
		t.Fatalf("valid capability tags = %v", got)
	}
	if got := parseCapabilityTags(`not-json`); len(got) != 0 {
		t.Fatalf("invalid capability tags = %v, want empty", got)
	}
}
