package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func resetA2ATaskStore() {
	globalTaskStore = &a2aTaskStore{tasks: make(map[string]*A2ATask)}
}

func callA2ATaskAPI(t *testing.T, handler http.Handler, request A2ATaskRequest) (*httptest.ResponseRecorder, A2ATaskResponse) {
	t.Helper()

	body, err := json.Marshal(request)
	if err != nil {
		t.Fatalf("marshal request: %v", err)
	}

	httpRequest := httptest.NewRequest(http.MethodPost, "/tasks", bytes.NewReader(body))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httpRequest)

	var response A2ATaskResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode response: %v; body=%s", err, recorder.Body.String())
	}
	return recorder, response
}

func TestA2ATaskGetRejectsUnknownTask(t *testing.T) {
	resetA2ATaskStore()
	handler := newA2AHandler("http://agenthub.test", nil, &A2ATLSConfig{})

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/get",
		Params:  map[string]any{"id": "missing-task"},
		ID:      "request-1",
	})

	if recorder.Code != http.StatusNotFound {
		t.Fatalf("expected status %d, got %d", http.StatusNotFound, recorder.Code)
	}
	if response.Error == nil || response.Error.Code != -32001 {
		t.Fatalf("expected task-not-found error, got %#v", response.Error)
	}
	if response.Result != nil {
		t.Fatalf("unknown task must not return a fabricated result: %#v", response.Result)
	}
}

func TestA2ATaskCancelRejectsUnknownTask(t *testing.T) {
	resetA2ATaskStore()
	handler := newA2AHandler("http://agenthub.test", nil, &A2ATLSConfig{})

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/cancel",
		Params:  map[string]any{"id": "missing-task"},
		ID:      "request-2",
	})

	if recorder.Code != http.StatusNotFound {
		t.Fatalf("expected status %d, got %d", http.StatusNotFound, recorder.Code)
	}
	if response.Error == nil || response.Error.Code != -32001 {
		t.Fatalf("expected task-not-found error, got %#v", response.Error)
	}
}

func TestA2ATaskSendRequiresConfiguredTarget(t *testing.T) {
	resetA2ATaskStore()
	handler := newA2AHandler("http://agenthub.test", nil, &A2ATLSConfig{})

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params:  map[string]any{"message": map[string]any{"role": "user"}},
		ID:      "request-3",
	})

	if recorder.Code != http.StatusNotImplemented {
		t.Fatalf("expected status %d, got %d", http.StatusNotImplemented, recorder.Code)
	}
	if response.Error == nil || response.Error.Code != -32004 {
		t.Fatalf("expected execution-not-configured error, got %#v", response.Error)
	}
	if len(globalTaskStore.tasks) != 0 {
		t.Fatalf("rejected send must not create an orphan task")
	}
}

func TestA2ATaskCancelUpdatesExistingTask(t *testing.T) {
	resetA2ATaskStore()
	globalTaskStore.put(&A2ATask{ID: "task-1", Status: "working"})
	handler := newA2AHandler("http://agenthub.test", nil, &A2ATLSConfig{})

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/cancel",
		Params:  map[string]any{"id": "task-1"},
		ID:      "request-4",
	})

	if recorder.Code != http.StatusOK || response.Error != nil {
		t.Fatalf("expected successful cancellation, status=%d error=%#v", recorder.Code, response.Error)
	}
	if task := globalTaskStore.get("task-1"); task == nil || task.Status != "cancelled" {
		t.Fatalf("expected stored task to be cancelled, got %#v", task)
	}
}
