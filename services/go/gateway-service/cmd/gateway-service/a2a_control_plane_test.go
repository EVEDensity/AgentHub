package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestA2AControlPlaneClientUsesMissionAdapterContract(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer control-token" {
			t.Errorf("expected authorization forwarding, got %q", r.Header.Get("Authorization"))
		}
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/a2a/tasks":
			var input a2aControlSubmit
			if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
				t.Fatalf("decode submit: %v", err)
			}
			if input.TaskID != "task-1" || input.WorkspaceID != "workspace-1" {
				t.Fatalf("unexpected submit body: %#v", input)
			}
			_ = json.NewEncoder(w).Encode(a2aControlTask{TaskID: input.TaskID, State: "submitted"})
		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/a2a/tasks":
			if r.URL.Query().Get("workspaceId") != "workspace-1" || r.URL.Query().Get("taskId") != "task-1" {
				t.Fatalf("unexpected lookup query: %s", r.URL.RawQuery)
			}
			_ = json.NewEncoder(w).Encode(a2aControlTask{TaskID: "task-1", State: "working"})
		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/a2a/tasks/inbound":
			if r.URL.Query().Get("workspaceId") != "workspace-1" || r.URL.Query().Get("sourceAgentUrl") != "https://source.test" || r.URL.Query().Get("taskId") != "task-1" {
				t.Fatalf("unexpected inbound lookup query: %s", r.URL.RawQuery)
			}
			_ = json.NewEncoder(w).Encode(a2aControlTask{
				TaskID: "task-1",
				State:  "completed",
				Artifacts: []A2AArtifact{{
					ArtifactID: "artifact-1",
					Name:       "artifact-1",
				}},
				Evidence: []A2AEvidence{{EvidenceID: "evidence-1", Verdict: "PASS"}},
			})
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/a2a/tasks/cancel":
			_ = json.NewEncoder(w).Encode(a2aControlTask{TaskID: "task-1", State: "canceled"})
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/a2a/tasks/fail":
			_ = json.NewEncoder(w).Encode(a2aControlTask{TaskID: "task-1", State: "failed"})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	client := newA2AControlPlaneClient(server.URL, server.Client())
	ctx := context.Background()

	if _, err := client.Submit(ctx, "Bearer control-token", a2aControlSubmit{
		TaskID: "task-1", WorkspaceID: "workspace-1", Objective: "review", AgentURL: "https://agent.test",
	}); err != nil {
		t.Fatalf("submit: %v", err)
	}
	if _, err := client.Get(ctx, "Bearer control-token", "workspace-1", "task-1"); err != nil {
		t.Fatalf("get: %v", err)
	}
	inbound, err := client.GetInbound(ctx, "Bearer control-token", "workspace-1", "https://source.test", "task-1")
	if err != nil {
		t.Fatalf("get inbound: %v", err)
	}
	if len(inbound.Artifacts) != 1 || len(inbound.Evidence) != 1 || inbound.Evidence[0].Verdict != "PASS" {
		t.Fatalf("expected decoded inbound result bundle, got %#v", inbound)
	}
	if _, err := client.Cancel(ctx, "Bearer control-token", "workspace-1", "task-1"); err != nil {
		t.Fatalf("cancel: %v", err)
	}
	if _, err := client.Fail(ctx, "Bearer control-token", "workspace-1", "task-1", "remote failed"); err != nil {
		t.Fatalf("fail: %v", err)
	}
}

func TestA2AControlPlaneClientPreservesHTTPErrorDetail(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"A2A task not found"}`))
	}))
	defer server.Close()
	client := newA2AControlPlaneClient(server.URL, server.Client())

	_, err := client.Get(context.Background(), "Bearer token", "workspace-1", "missing")
	controlErr, ok := err.(*a2aControlPlaneError)
	if !ok || controlErr.StatusCode != http.StatusNotFound || controlErr.Detail != "A2A task not found" {
		t.Fatalf("expected structured control-plane error, got %#v", err)
	}
}
