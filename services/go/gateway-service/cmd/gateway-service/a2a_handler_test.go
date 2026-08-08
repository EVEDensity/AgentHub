package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type fakeA2AControlPlane struct {
	tasks       map[string]*a2aControlTask
	authorities []string
	submits     []a2aControlSubmit
	fails       []string
}

func newFakeA2AControlPlane() *fakeA2AControlPlane {
	return &fakeA2AControlPlane{tasks: make(map[string]*a2aControlTask)}
}

func (fake *fakeA2AControlPlane) Submit(_ context.Context, authorization string, input a2aControlSubmit) (*a2aControlTask, error) {
	fake.authorities = append(fake.authorities, authorization)
	fake.submits = append(fake.submits, input)
	if existing := fake.tasks[input.TaskID]; existing != nil {
		return existing, nil
	}
	task := &a2aControlTask{
		TaskID:         input.TaskID,
		AgentURL:       input.AgentURL,
		State:          "submitted",
		MissionID:      "mission-" + input.TaskID,
		MissionStatus:  "RUNNING",
		WorkUnitID:     "work-unit-" + input.TaskID,
		WorkUnitStatus: "PENDING",
	}
	fake.tasks[input.TaskID] = task
	return task, nil
}

func (fake *fakeA2AControlPlane) Get(_ context.Context, authorization, _workspaceID, taskID string) (*a2aControlTask, error) {
	fake.authorities = append(fake.authorities, authorization)
	if task := fake.tasks[taskID]; task != nil {
		return task, nil
	}
	return nil, &a2aControlPlaneError{StatusCode: http.StatusNotFound, Detail: "A2A task not found"}
}

func (fake *fakeA2AControlPlane) Cancel(_ context.Context, authorization, _workspaceID, taskID string) (*a2aControlTask, error) {
	fake.authorities = append(fake.authorities, authorization)
	task := fake.tasks[taskID]
	if task == nil {
		return nil, &a2aControlPlaneError{StatusCode: http.StatusNotFound, Detail: "A2A task not found"}
	}
	task.State = "canceled"
	task.MissionStatus = "CANCELLED"
	task.WorkUnitStatus = "CANCELLED"
	return task, nil
}

func (fake *fakeA2AControlPlane) Fail(_ context.Context, authorization, _workspaceID, taskID, reason string) (*a2aControlTask, error) {
	fake.authorities = append(fake.authorities, authorization)
	fake.fails = append(fake.fails, reason)
	task := fake.tasks[taskID]
	if task == nil {
		return nil, &a2aControlPlaneError{StatusCode: http.StatusNotFound, Detail: "A2A task not found"}
	}
	task.State = "failed"
	task.MissionStatus = "FAILED"
	task.WorkUnitStatus = "FAILED"
	return task, nil
}

func callA2ATaskAPI(t *testing.T, handler http.Handler, request A2ATaskRequest, authorization string) (*httptest.ResponseRecorder, A2ATaskResponse) {
	t.Helper()
	body, err := json.Marshal(request)
	if err != nil {
		t.Fatalf("marshal request: %v", err)
	}
	httpRequest := httptest.NewRequest(http.MethodPost, "/tasks", bytes.NewReader(body))
	if authorization != "" {
		httpRequest.Header.Set("Authorization", authorization)
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httpRequest)
	var response A2ATaskResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode response: %v; body=%s", err, recorder.Body.String())
	}
	return recorder, response
}

func TestA2ATaskGetRejectsUnknownTaskFromControlPlane(t *testing.T) {
	control := newFakeA2AControlPlane()
	handler := newA2AHandler("http://agenthub.test", nil, &A2ATLSConfig{}, control)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/get",
		Params:  map[string]any{"id": "missing-task", "workspaceId": "workspace-1"},
		ID:      "request-1",
	}, "Bearer test-token")

	if recorder.Code != http.StatusNotFound || response.Error == nil || response.Error.Code != -32001 {
		t.Fatalf("expected control-plane task-not-found, status=%d response=%#v", recorder.Code, response)
	}
	if response.Result != nil {
		t.Fatalf("unknown task must not return a fabricated result: %#v", response.Result)
	}
}

func TestA2ATaskSendRequiresConfiguredTarget(t *testing.T) {
	handler := newA2AHandler("http://agenthub.test", nil, &A2ATLSConfig{}, nil)
	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params:  map[string]any{"workspaceId": "workspace-1", "message": map[string]any{"role": "user"}},
		ID:      int64(3),
	}, "Bearer test-token")

	if recorder.Code != http.StatusNotImplemented || response.Error == nil || response.Error.Code != -32004 {
		t.Fatalf("expected target validation error, status=%d response=%#v", recorder.Code, response)
	}
}

func TestA2ATaskSendPersistsBeforeForwardAndForwardsAuth(t *testing.T) {
	control := newFakeA2AControlPlane()
	remote := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request A2ATaskRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Errorf("decode remote request: %v", err)
		}
		if request.Params["id"] != "task-1" {
			t.Errorf("expected durable task id in remote request, got %#v", request.Params["id"])
		}
		writeJSON(w, http.StatusOK, A2ATaskResponse{JSONRPC: "2.0", Result: map[string]any{"status": "working"}, ID: request.ID})
	}))
	defer remote.Close()
	handler := newA2AHandler("http://agenthub.test", nil, &A2ATLSConfig{}, control)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params: map[string]any{
			"id":          "task-1",
			"workspaceId": "workspace-1",
			"agentUrl":    remote.URL,
			"message":     map[string]any{"role": "user", "parts": []any{map[string]any{"type": "text", "text": "review"}}},
		},
		ID: int64(4),
	}, "Bearer test-token")

	if recorder.Code != http.StatusOK || response.Error != nil {
		t.Fatalf("expected successful durable submit, status=%d response=%#v", recorder.Code, response)
	}
	if response.Result == nil {
		t.Fatal("expected task projection")
	}
	result, ok := response.Result.(map[string]any)
	if !ok || result["id"] != "task-1" || result["status"] != "submitted" {
		t.Fatalf("expected control-plane projection, got %#v", response.Result)
	}
	if len(control.submits) != 1 || control.submits[0].Objective != "review" {
		t.Fatalf("expected control-plane submit, got %#v", control.submits)
	}
	if control.authorities[0] != "Bearer test-token" {
		t.Fatalf("expected Authorization forwarding, got %#v", control.authorities)
	}
}

func TestA2ATaskSendRecordsRemoteFailureInControlPlane(t *testing.T) {
	control := newFakeA2AControlPlane()
	remote := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusBadGateway, A2ATaskResponse{
			JSONRPC: "2.0",
			Error:   &A2AError{Code: -32010, Message: "remote unavailable"},
			ID:      "remote-1",
		})
	}))
	defer remote.Close()
	handler := newA2AHandler("http://agenthub.test", nil, &A2ATLSConfig{}, control)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params: map[string]any{
			"id":          "task-failed",
			"workspaceId": "workspace-1",
			"agentUrl":    remote.URL,
			"message":     map[string]any{"role": "user", "parts": []any{map[string]any{"type": "text", "text": "fail me"}}},
		},
		ID: "request-failed",
	}, "Bearer test-token")

	if recorder.Code != http.StatusOK || response.Error != nil {
		t.Fatalf("expected durable failure result, status=%d response=%#v", recorder.Code, response)
	}
	result := response.Result.(map[string]any)
	if result["status"] != "failed" || len(control.fails) != 1 || !strings.Contains(control.fails[0], "remote A2A error") {
		t.Fatalf("expected failure write-back, result=%#v failures=%#v", result, control.fails)
	}
}

func TestA2ATaskCancelUsesControlPlaneAndForwardsRemoteCancel(t *testing.T) {
	control := newFakeA2AControlPlane()
	control.tasks["task-cancel"] = &a2aControlTask{
		TaskID: "task-cancel", AgentURL: "http://127.0.0.1:1", State: "working",
		MissionStatus: "RUNNING", WorkUnitStatus: "RUNNING",
	}
	remote := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/tasks" {
			t.Errorf("expected remote /tasks path, got %s", r.URL.Path)
		}
		writeJSON(w, http.StatusOK, A2ATaskResponse{JSONRPC: "2.0", Result: map[string]any{"status": "canceled"}, ID: "remote-cancel"})
	}))
	defer remote.Close()
	control.tasks["task-cancel"].AgentURL = remote.URL
	handler := newA2AHandler("http://agenthub.test", nil, &A2ATLSConfig{}, control)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/cancel",
		Params:  map[string]any{"id": "task-cancel", "workspaceId": "workspace-1"},
		ID:      "request-cancel",
	}, "Bearer test-token")

	if recorder.Code != http.StatusOK || response.Error != nil {
		t.Fatalf("expected successful cancellation, status=%d response=%#v", recorder.Code, response)
	}
	if response.Result.(map[string]any)["status"] != "canceled" {
		t.Fatalf("expected canceled control projection, got %#v", response.Result)
	}
}
