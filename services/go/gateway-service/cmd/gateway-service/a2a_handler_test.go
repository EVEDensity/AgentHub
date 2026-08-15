package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/hex"
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
	accepts     []a2aControlAccept
	fails       []string
	acceptErr   error
}

func (fake *fakeA2AControlPlane) Accept(_ context.Context, authorization string, input a2aControlAccept) (*a2aControlTask, error) {
	fake.authorities = append(fake.authorities, authorization)
	fake.accepts = append(fake.accepts, input)
	if fake.acceptErr != nil {
		return nil, fake.acceptErr
	}
	if existing := fake.tasks[input.TaskID]; existing != nil {
		return existing, nil
	}
	task := &a2aControlTask{
		TaskID:         input.TaskID,
		AgentURL:       input.SourceAgentURL,
		State:          "submitted",
		MissionID:      "mission-inbound-" + input.TaskID,
		MissionStatus:  "RUNNING",
		WorkUnitID:     "work-unit-inbound-" + input.TaskID,
		WorkUnitStatus: "PENDING",
	}
	fake.tasks[input.TaskID] = task
	return task, nil
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

func (fake *fakeA2AControlPlane) CancelInbound(_ context.Context, authorization, _workspaceID, sourceAgentURL, taskID string) (*a2aControlTask, error) {
	fake.authorities = append(fake.authorities, authorization)
	task := fake.tasks[taskID]
	if task == nil || task.AgentURL != sourceAgentURL {
		return nil, &a2aControlPlaneError{StatusCode: http.StatusNotFound, Detail: "inbound A2A task not found"}
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

func newA2ATestAgentServer(t *testing.T, mutateCard func(*AgentCard), taskHandler http.HandlerFunc) *httptest.Server {
	t.Helper()
	var server *httptest.Server
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/.well-known/agent-card.json":
			card := AgentCard{
				ProtocolVersion: "1.0",
				Name:            "Remote Agent",
				URL:             server.URL,
				Skills: []AgentSkill{
					{ID: "repository.read", Tags: []string{"review"}},
				},
				Endpoints: AgentEndpoints{TaskAPI: server.URL + "/tasks"},
			}
			if mutateCard != nil {
				mutateCard(&card)
			}
			writeJSON(w, http.StatusOK, card)
		case r.Method == http.MethodPost && r.URL.Path == "/tasks":
			if taskHandler == nil {
				t.Errorf("unexpected remote task request")
				http.Error(w, "unexpected task request", http.StatusInternalServerError)
				return
			}
			taskHandler(w, r)
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(server.Close)
	return server
}

func newTestA2AHandler(control a2aControlPlane) http.Handler {
	return newTestA2AHandlerWithPolicy(A2ATrustPolicy{AllowUnsigned: true}, nil, control)
}

func newTestA2AHandlerWithPolicy(policy A2ATrustPolicy, signer A2ACardSigner, control a2aControlPlane) http.Handler {
	return newTestA2AHandlerWithPolicyAndCredentials(
		policy,
		signer,
		&A2APeerCredentials{bearerByOrigin: map[string]string{}},
		control,
	)
}

func newTestA2AHandlerWithPolicyAndCredentials(policy A2ATrustPolicy, signer A2ACardSigner, credentials *A2APeerCredentials, control a2aControlPlane) http.Handler {
	handler, err := newA2AHandlerWithTrustPolicy(
		"http://agenthub.test",
		nil,
		&A2ATLSConfig{},
		policy,
		signer,
		credentials,
		control,
	)
	if err != nil {
		panic(err)
	}
	return handler
}

func TestA2ATaskGetRejectsUnknownTaskFromControlPlane(t *testing.T) {
	control := newFakeA2AControlPlane()
	handler := newTestA2AHandler(control)

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
	handler := newTestA2AHandler(nil)
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

func TestA2ATaskRejectsInvalidJSONRPCEnvelope(t *testing.T) {
	control := newFakeA2AControlPlane()
	handler := newTestA2AHandler(control)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "1.0",
		Method:  "tasks/send",
		Params:  map[string]any{"workspaceId": "workspace-1"},
		ID:      "invalid-envelope",
	}, "Bearer test-token")

	if recorder.Code != http.StatusBadRequest || response.Error == nil || response.Error.Code != -32600 {
		t.Fatalf("expected JSON-RPC invalid request, status=%d response=%#v", recorder.Code, response)
	}
	if len(control.submits) != 0 {
		t.Fatalf("invalid JSON-RPC envelope must not submit a task: %#v", control.submits)
	}
}

func TestA2ATaskSendPersistsBeforeForwardAndForwardsAuth(t *testing.T) {
	control := newFakeA2AControlPlane()
	remote := newA2ATestAgentServer(t, nil, func(w http.ResponseWriter, r *http.Request) {
		var request A2ATaskRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Errorf("decode remote request: %v", err)
		}
		if request.Params["id"] != "task-1" {
			t.Errorf("expected durable task id in remote request, got %#v", request.Params["id"])
		}
		if _, present := request.Params["agentUrl"]; present {
			t.Error("outbound routing agentUrl must not cross the A2A inbox boundary")
		}
		if request.Params["sourceAgentUrl"] != "http://agenthub.test" {
			t.Errorf("expected authenticated source Agent URL, got %#v", request.Params["sourceAgentUrl"])
		}
		if authorization := r.Header.Get("Authorization"); authorization != "" {
			t.Errorf("caller Authorization must not be forwarded to the peer, got %q", authorization)
		}
		capabilities, ok := request.Params["requiredCapabilities"].([]any)
		if !ok || len(capabilities) != 1 || capabilities[0] != "repository.read" {
			t.Errorf("expected normalized capabilities in remote request, got %#v", request.Params["requiredCapabilities"])
		}
		writeJSON(w, http.StatusOK, A2ATaskResponse{JSONRPC: "2.0", Result: map[string]any{"status": "working"}, ID: request.ID})
	})
	handler := newTestA2AHandler(control)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params: map[string]any{
			"id":          "task-1",
			"workspaceId": "workspace-1",
			"agentUrl":    remote.URL,
			"requiredCapabilities": []any{
				" repository.read ",
			},
			"message": map[string]any{"role": "user", "parts": []any{map[string]any{"type": "text", "text": "review"}}},
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
	if len(control.submits) != 1 || control.submits[0].Objective != "review" || len(control.submits[0].RequiredCapabilities) != 1 || control.submits[0].RequiredCapabilities[0] != "repository.read" {
		t.Fatalf("expected control-plane submit, got %#v", control.submits)
	}
	if control.authorities[0] != "Bearer test-token" {
		t.Fatalf("expected Authorization forwarding, got %#v", control.authorities)
	}
}

func TestA2ATaskSendUsesReceiverIssuedBearerCredential(t *testing.T) {
	control := newFakeA2AControlPlane()
	const peerToken = "receiver-issued-token"
	remote := newA2ATestAgentServer(t, func(card *AgentCard) {
		card.AuthSchemes = []AuthScheme{{Type: "bearer"}}
	}, func(w http.ResponseWriter, r *http.Request) {
		if authorization := r.Header.Get("Authorization"); authorization != "Bearer "+peerToken {
			t.Errorf("expected receiver-issued credential, got %q", authorization)
		}
		var request A2ATaskRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Errorf("decode remote request: %v", err)
		}
		writeJSON(w, http.StatusOK, A2ATaskResponse{JSONRPC: "2.0", Result: map[string]any{"status": "working"}, ID: request.ID})
	})
	origin, err := canonicalA2AOrigin(remote.URL)
	if err != nil {
		t.Fatalf("canonicalize remote origin: %v", err)
	}
	handler := newTestA2AHandlerWithPolicyAndCredentials(
		A2ATrustPolicy{AllowUnsigned: true},
		nil,
		&A2APeerCredentials{bearerByOrigin: map[string]string{origin: peerToken}},
		control,
	)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params: map[string]any{
			"id":          "task-peer-auth",
			"workspaceId": "workspace-1",
			"agentUrl":    remote.URL,
			"message":     map[string]any{"role": "user", "parts": []any{map[string]any{"type": "text", "text": "authenticate"}}},
		},
		ID: "request-peer-auth",
	}, "Bearer caller-token")

	if recorder.Code != http.StatusOK || response.Error != nil || response.Result.(map[string]any)["status"] != "submitted" {
		t.Fatalf("expected receiver-authenticated dispatch, status=%d response=%#v", recorder.Code, response)
	}
}

func TestA2ATaskSendFailsDurablyWhenBearerCredentialIsMissing(t *testing.T) {
	control := newFakeA2AControlPlane()
	remote := newA2ATestAgentServer(t, func(card *AgentCard) {
		card.AuthSchemes = []AuthScheme{{Type: "Bearer"}}
	}, func(w http.ResponseWriter, _ *http.Request) {
		t.Error("peer endpoint must not be called without its receiver-issued credential")
		http.Error(w, "unexpected", http.StatusInternalServerError)
	})
	handler := newTestA2AHandler(control)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params: map[string]any{
			"id":          "task-missing-peer-auth",
			"workspaceId": "workspace-1",
			"agentUrl":    remote.URL,
			"message":     map[string]any{"role": "user", "parts": []any{map[string]any{"type": "text", "text": "authenticate"}}},
		},
		ID: "request-missing-peer-auth",
	}, "Bearer caller-token")

	if recorder.Code != http.StatusOK || response.Error != nil {
		t.Fatalf("expected durable failure projection, status=%d response=%#v", recorder.Code, response)
	}
	if response.Result.(map[string]any)["status"] != "failed" || len(control.fails) != 1 || !strings.Contains(control.fails[0], "receiver-issued credential") {
		t.Fatalf("expected missing peer credential failure, result=%#v failures=%#v", response.Result, control.fails)
	}
}

func TestA2ATaskSendDoesNotForwardPeerCredentialAcrossRedirect(t *testing.T) {
	control := newFakeA2AControlPlane()
	const peerToken = "redirect-protected-peer-token"
	redirectedRequests := make(chan string, 1)
	redirectTarget := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		redirectedRequests <- r.Header.Get("Authorization")
	}))
	t.Cleanup(redirectTarget.Close)
	remote := newA2ATestAgentServer(t, func(card *AgentCard) {
		card.AuthSchemes = []AuthScheme{{Type: "bearer"}}
	}, func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, redirectTarget.URL, http.StatusTemporaryRedirect)
	})
	origin, err := canonicalA2AOrigin(remote.URL)
	if err != nil {
		t.Fatalf("canonicalize remote origin: %v", err)
	}
	handler := newTestA2AHandlerWithPolicyAndCredentials(
		A2ATrustPolicy{AllowUnsigned: true},
		nil,
		&A2APeerCredentials{bearerByOrigin: map[string]string{origin: peerToken}},
		control,
	)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params: map[string]any{
			"id":          "task-peer-auth-redirect",
			"workspaceId": "workspace-1",
			"agentUrl":    remote.URL,
			"message":     map[string]any{"role": "user", "parts": []any{map[string]any{"type": "text", "text": "do not leak"}}},
		},
		ID: "request-peer-auth-redirect",
	}, "Bearer caller-token")

	if recorder.Code != http.StatusOK || response.Error != nil || response.Result.(map[string]any)["status"] != "failed" {
		t.Fatalf("expected durable redirect failure, status=%d response=%#v", recorder.Code, response)
	}
	if len(control.fails) != 1 || !strings.Contains(control.fails[0], "redirect crossed") || strings.Contains(control.fails[0], peerToken) {
		t.Fatalf("expected redacted cross-origin redirect failure, failures=%#v", control.fails)
	}
	select {
	case authorization := <-redirectedRequests:
		t.Fatalf("cross-origin redirect received peer credential %q", authorization)
	default:
	}
}

func TestA2ATaskSendRecordsRemoteFailureInControlPlane(t *testing.T) {
	control := newFakeA2AControlPlane()
	remote := newA2ATestAgentServer(t, nil, func(w http.ResponseWriter, r *http.Request) {
		var request A2ATaskRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Errorf("decode remote request: %v", err)
		}
		writeJSON(w, http.StatusBadGateway, A2ATaskResponse{
			JSONRPC: "2.0",
			Error:   &A2AError{Code: -32010, Message: "remote unavailable"},
			ID:      request.ID,
		})
	})
	handler := newTestA2AHandler(control)

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

func TestA2ATaskSendRejectsUnsupportedAgentCapabilityAfterPersistence(t *testing.T) {
	control := newFakeA2AControlPlane()
	taskRequests := make(chan struct{}, 1)
	remote := newA2ATestAgentServer(t, nil, func(w http.ResponseWriter, _ *http.Request) {
		taskRequests <- struct{}{}
		writeJSON(w, http.StatusOK, A2ATaskResponse{JSONRPC: "2.0", Result: map[string]any{"status": "working"}})
	})
	handler := newTestA2AHandler(control)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params: map[string]any{
			"id":                   "task-capability-mismatch",
			"workspaceId":          "workspace-1",
			"agentUrl":             remote.URL,
			"requiredCapabilities": []any{"repository.write"},
			"message":              map[string]any{"role": "user", "parts": []any{map[string]any{"type": "text", "text": "write"}}},
		},
		ID: "request-capability-mismatch",
	}, "Bearer test-token")

	if recorder.Code != http.StatusOK || response.Error != nil {
		t.Fatalf("expected durable failure result, status=%d response=%#v", recorder.Code, response)
	}
	if len(control.submits) != 1 || len(control.fails) != 1 {
		t.Fatalf("expected submit before failure write-back, submits=%#v failures=%#v", control.submits, control.fails)
	}
	if response.Result.(map[string]any)["status"] != "failed" || !strings.Contains(control.fails[0], "required capabilities") {
		t.Fatalf("expected capability failure projection, result=%#v failures=%#v", response.Result, control.fails)
	}
	select {
	case <-taskRequests:
		t.Fatal("unsupported Agent must not receive the delegated task")
	default:
	}
}

func TestA2ATaskCancelUsesControlPlaneAndForwardsRemoteCancel(t *testing.T) {
	control := newFakeA2AControlPlane()
	control.tasks["task-cancel"] = &a2aControlTask{
		TaskID: "task-cancel", AgentURL: "http://127.0.0.1:1", State: "working",
		MissionStatus: "RUNNING", WorkUnitStatus: "RUNNING",
	}
	remote := newA2ATestAgentServer(t, nil, func(w http.ResponseWriter, r *http.Request) {
		var request A2ATaskRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Errorf("decode remote request: %v", err)
		}
		writeJSON(w, http.StatusOK, A2ATaskResponse{JSONRPC: "2.0", Result: map[string]any{"status": "canceled"}, ID: request.ID})
	})
	control.tasks["task-cancel"].AgentURL = remote.URL
	handler := newTestA2AHandler(control)

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

func TestA2ATaskSendRejectsMismatchedRemoteResponseID(t *testing.T) {
	control := newFakeA2AControlPlane()
	remote := newA2ATestAgentServer(t, nil, func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, A2ATaskResponse{
			JSONRPC: "2.0",
			Result:  map[string]any{"status": "working"},
			ID:      "wrong-request-id",
		})
	})
	handler := newTestA2AHandler(control)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params: map[string]any{
			"id":          "task-response-id",
			"workspaceId": "workspace-1",
			"agentUrl":    remote.URL,
			"message":     map[string]any{"role": "user", "parts": []any{map[string]any{"type": "text", "text": "response id"}}},
		},
		ID: "request-response-id",
	}, "Bearer test-token")

	if recorder.Code != http.StatusOK || response.Error != nil {
		t.Fatalf("expected durable failure result, status=%d response=%#v", recorder.Code, response)
	}
	if response.Result.(map[string]any)["status"] != "failed" || len(control.fails) != 1 || !strings.Contains(control.fails[0], "mismatched response id") {
		t.Fatalf("expected response-id failure write-back, result=%#v failures=%#v", response.Result, control.fails)
	}
}

func TestA2ATaskSendRejectsOversizedRemoteResponse(t *testing.T) {
	control := newFakeA2AControlPlane()
	remote := newA2ATestAgentServer(t, nil, func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(bytes.Repeat([]byte("x"), int(maxA2ATaskResponseBytes)+1))
	})
	handler := newTestA2AHandler(control)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params: map[string]any{
			"id":          "task-oversized-response",
			"workspaceId": "workspace-1",
			"agentUrl":    remote.URL,
			"message":     map[string]any{"role": "user", "parts": []any{map[string]any{"type": "text", "text": "oversized response"}}},
		},
		ID: "request-oversized-response",
	}, "Bearer test-token")

	if recorder.Code != http.StatusOK || response.Error != nil {
		t.Fatalf("expected durable failure result, status=%d response=%#v", recorder.Code, response)
	}
	if response.Result.(map[string]any)["status"] != "failed" || len(control.fails) != 1 || !strings.Contains(control.fails[0], "response exceeds") {
		t.Fatalf("expected oversized response failure write-back, result=%#v failures=%#v", response.Result, control.fails)
	}
}

func TestExtractRequiredCapabilitiesRejectsMalformedValues(t *testing.T) {
	tests := []struct {
		name   string
		value  any
		absent bool
	}{
		{name: "absent", absent: true},
		{name: "not array", value: "repository.read"},
		{name: "non string", value: []any{"repository.read", 1}},
		{name: "empty", value: []any{" "}},
		{name: "duplicate", value: []any{"repository.read", " Repository.Read "}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			params := make(map[string]any)
			if !test.absent {
				params["requiredCapabilities"] = test.value
			}
			capabilities, err := extractRequiredCapabilities(params)
			if test.absent {
				if err != nil || capabilities != nil {
					t.Fatalf("absent capabilities should be accepted, capabilities=%#v err=%v", capabilities, err)
				}
				return
			}
			if err == nil {
				t.Fatalf("expected malformed capabilities to fail, got %#v", capabilities)
			}
		})
	}
}

func TestProbeAgentCardRejectsCrossOriginTaskEndpoint(t *testing.T) {
	remote := newA2ATestAgentServer(t, func(card *AgentCard) {
		card.Endpoints.TaskAPI = "https://other-agent.test/tasks"
	}, nil)

	_, err := probeAgentCard(context.Background(), remote.Client(), remote.URL, nil, A2ATrustPolicy{AllowUnsigned: true})
	if err == nil || !strings.Contains(err.Error(), "configured agent origin") {
		t.Fatalf("expected cross-origin endpoint rejection, got %v", err)
	}
}

func TestProbeAgentCardRejectsUnsupportedProtocolVersion(t *testing.T) {
	remote := newA2ATestAgentServer(t, func(card *AgentCard) {
		card.ProtocolVersion = "2.0"
	}, nil)

	_, err := probeAgentCard(context.Background(), remote.Client(), remote.URL, nil, A2ATrustPolicy{AllowUnsigned: true})
	if err == nil || !strings.Contains(err.Error(), "unsupported A2A protocol version") {
		t.Fatalf("expected protocol version rejection, got %v", err)
	}
}

func TestVerifyAgentCardSignature(t *testing.T) {
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("generate Ed25519 key: %v", err)
	}
	card := AgentCard{
		ProtocolVersion: "1.0",
		Name:            "Signed Agent",
		URL:             "https://agent.test",
		Security: &AgentSecurity{
			PublicKey:    hex.EncodeToString(publicKey),
			KeyAlgorithm: "ed25519",
		},
	}
	payload, err := json.Marshal(&card)
	if err != nil {
		t.Fatalf("marshal unsigned card: %v", err)
	}
	signature := ed25519.Sign(privateKey, payload)
	card.Signature = hex.EncodeToString(signature)
	if err := VerifyAgentCardSignature(&card); err != nil {
		t.Fatalf("valid Ed25519 signature rejected: %v", err)
	}

	invalidSignature := append([]byte(nil), signature...)
	invalidSignature[0] ^= 0xff
	card.Signature = hex.EncodeToString(invalidSignature)
	if err := VerifyAgentCardSignature(&card); err == nil || !strings.Contains(err.Error(), "verification failed") {
		t.Fatalf("invalid Ed25519 signature accepted: %v", err)
	}

	card.Signature = hex.EncodeToString(signature)
	card.Security.KeyAlgorithm = "rsa"
	if err := VerifyAgentCardSignature(&card); err == nil || !strings.Contains(err.Error(), "unsupported") {
		t.Fatalf("unsupported signature algorithm accepted: %v", err)
	}
}

func TestA2ATrustPolicyFromEnvDefaultsFailClosed(t *testing.T) {
	t.Setenv("A2A_ALLOW_UNSIGNED_CARDS", "")
	t.Setenv("A2A_REQUIRE_PINNED_KEYS", "")
	t.Setenv("A2A_TRUSTED_PUBLIC_KEYS_JSON", "")

	policy, err := a2aTrustPolicyFromEnv()
	if err != nil {
		t.Fatalf("parse default trust policy: %v", err)
	}
	if policy.AllowUnsigned || policy.RequirePinnedKey || len(policy.TrustedKeys) != 0 {
		t.Fatalf("expected fail-closed default trust policy, got %#v", policy)
	}
}

func TestA2ATrustPolicyFromEnvSupportsPinnedKeyRotation(t *testing.T) {
	firstPublicKey, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("generate first Ed25519 key: %v", err)
	}
	secondPublicKey, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("generate second Ed25519 key: %v", err)
	}
	configured, err := json.Marshal(map[string][]string{
		"https://agent.test": {
			hex.EncodeToString(firstPublicKey),
			hex.EncodeToString(secondPublicKey),
		},
	})
	if err != nil {
		t.Fatalf("marshal trusted keys: %v", err)
	}
	t.Setenv("A2A_ALLOW_UNSIGNED_CARDS", "false")
	t.Setenv("A2A_REQUIRE_PINNED_KEYS", "true")
	t.Setenv("A2A_TRUSTED_PUBLIC_KEYS_JSON", string(configured))

	policy, err := a2aTrustPolicyFromEnv()
	if err != nil {
		t.Fatalf("parse rotated trust keys: %v", err)
	}
	if !policy.RequirePinnedKey || len(policy.TrustedKeys["https://agent.test"]) != 2 {
		t.Fatalf("expected two trusted rotation keys, got %#v", policy)
	}
}

func TestA2ATrustPolicyFromEnvRejectsInvalidConfiguration(t *testing.T) {
	t.Setenv("A2A_ALLOW_UNSIGNED_CARDS", "sometimes")
	t.Setenv("A2A_REQUIRE_PINNED_KEYS", "")
	t.Setenv("A2A_TRUSTED_PUBLIC_KEYS_JSON", "")
	if _, err := a2aTrustPolicyFromEnv(); err == nil || !strings.Contains(err.Error(), "must be a boolean") {
		t.Fatalf("expected invalid boolean configuration to fail, got %v", err)
	}

	t.Setenv("A2A_ALLOW_UNSIGNED_CARDS", "false")
	t.Setenv("A2A_REQUIRE_PINNED_KEYS", "true")
	if _, err := a2aTrustPolicyFromEnv(); err == nil || !strings.Contains(err.Error(), "requires") {
		t.Fatalf("expected missing trusted keys to fail, got %v", err)
	}

	t.Setenv("A2A_ALLOW_UNSIGNED_CARDS", "true")
	if _, err := a2aTrustPolicyFromEnv(); err == nil || !strings.Contains(err.Error(), "cannot both be true") {
		t.Fatalf("expected conflicting trust modes to fail, got %v", err)
	}

	t.Setenv("A2A_ALLOW_UNSIGNED_CARDS", "false")
	t.Setenv("A2A_TRUSTED_PUBLIC_KEYS_JSON", "{}")
	if _, err := a2aTrustPolicyFromEnv(); err == nil || !strings.Contains(err.Error(), "at least one") {
		t.Fatalf("expected empty trusted origin map to fail, got %v", err)
	}
}

func TestVerifyAgentCardTrustEnforcesUnsignedPolicy(t *testing.T) {
	card := &AgentCard{Name: "Unsigned Agent", URL: "https://agent.test"}
	if err := VerifyAgentCardTrust(card, card.URL, A2ATrustPolicy{}); err == nil || !strings.Contains(err.Error(), "unsigned") {
		t.Fatalf("strict policy accepted unsigned card: %v", err)
	}
	if err := VerifyAgentCardTrust(card, card.URL, A2ATrustPolicy{AllowUnsigned: true}); err != nil {
		t.Fatalf("compatibility policy rejected unsigned card: %v", err)
	}
}

func TestVerifyAgentCardTrustSupportsPinnedKeyRotation(t *testing.T) {
	firstPublicKey, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("generate first Ed25519 key: %v", err)
	}
	secondPublicKey, secondPrivateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("generate second Ed25519 key: %v", err)
	}
	card := AgentCard{
		ProtocolVersion: "1.0",
		Name:            "Rotating Agent",
		URL:             "https://agent.test",
		Security: &AgentSecurity{
			PublicKey:    hex.EncodeToString(secondPublicKey),
			KeyAlgorithm: "ed25519",
		},
	}
	payload, err := json.Marshal(&card)
	if err != nil {
		t.Fatalf("marshal unsigned rotating card: %v", err)
	}
	card.Signature = hex.EncodeToString(ed25519.Sign(secondPrivateKey, payload))
	origin := "https://agent.test"
	policy := A2ATrustPolicy{
		RequirePinnedKey: true,
		TrustedKeys: map[string]map[string]struct{}{
			origin: {
				hex.EncodeToString(firstPublicKey):  {},
				hex.EncodeToString(secondPublicKey): {},
			},
		},
	}
	if err := VerifyAgentCardTrust(&card, origin+"/a2a", policy); err != nil {
		t.Fatalf("rotated trusted key rejected: %v", err)
	}

	policy.TrustedKeys[origin] = map[string]struct{}{hex.EncodeToString(firstPublicKey): {}}
	if err := VerifyAgentCardTrust(&card, origin, policy); err == nil || !strings.Contains(err.Error(), "not trusted") {
		t.Fatalf("untrusted rotated key accepted: %v", err)
	}
}

func TestA2ATaskSendWritesBackUnsignedTrustFailure(t *testing.T) {
	control := newFakeA2AControlPlane()
	taskRequests := make(chan struct{}, 1)
	remote := newA2ATestAgentServer(t, nil, func(w http.ResponseWriter, _ *http.Request) {
		taskRequests <- struct{}{}
		writeJSON(w, http.StatusOK, A2ATaskResponse{JSONRPC: "2.0", Result: map[string]any{"status": "working"}})
	})
	handler := newTestA2AHandlerWithPolicy(A2ATrustPolicy{}, nil, control)

	recorder, response := callA2ATaskAPI(t, handler, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params: map[string]any{
			"id":          "task-unsigned-card",
			"workspaceId": "workspace-1",
			"agentUrl":    remote.URL,
			"message":     map[string]any{"role": "user", "parts": []any{map[string]any{"type": "text", "text": "strict trust"}}},
		},
		ID: "request-unsigned-card",
	}, "Bearer test-token")

	if recorder.Code != http.StatusOK || response.Error != nil {
		t.Fatalf("expected durable failure result, status=%d response=%#v", recorder.Code, response)
	}
	if len(control.submits) != 1 || len(control.fails) != 1 || !strings.Contains(control.fails[0], "unsigned Agent Card") {
		t.Fatalf("expected unsigned trust failure write-back, submits=%#v failures=%#v", control.submits, control.fails)
	}
	select {
	case <-taskRequests:
		t.Fatal("strict trust policy must reject unsigned card before remote task dispatch")
	default:
	}
}

func TestA2ARegistryRejectsUnsignedCardUnderStrictPolicy(t *testing.T) {
	handler := newTestA2AHandlerWithPolicy(A2ATrustPolicy{}, nil, newFakeA2AControlPlane())
	cardBody, err := json.Marshal(AgentCard{
		ProtocolVersion: "1.0",
		Name:            "Unsigned Registry Agent",
		URL:             "https://unsigned-registry-agent.test",
		Endpoints:       AgentEndpoints{TaskAPI: "https://unsigned-registry-agent.test/tasks"},
	})
	if err != nil {
		t.Fatalf("marshal registry card: %v", err)
	}
	request := httptest.NewRequest(http.MethodPost, "/registry", bytes.NewReader(cardBody))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusBadRequest || !strings.Contains(recorder.Body.String(), "unsigned Agent Card") {
		t.Fatalf("expected strict registry trust rejection, status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestA2ATrustStatusDoesNotExposePublicKeys(t *testing.T) {
	policy := A2ATrustPolicy{
		RequirePinnedKey: true,
		TrustedKeys: map[string]map[string]struct{}{
			"https://agent.test": {"sensitive-key-material": {}},
		},
	}
	handler := newTestA2AHandlerWithPolicy(policy, nil, newFakeA2AControlPlane())
	request := httptest.NewRequest(http.MethodGet, "/trust-status", nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("expected trust status, status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if strings.Contains(recorder.Body.String(), "sensitive-key-material") {
		t.Fatalf("trust status exposed public key material: %s", recorder.Body.String())
	}
	var status map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &status); err != nil {
		t.Fatalf("decode trust status: %v", err)
	}
	if status["allow_unsigned"] != false || status["require_pinned_key"] != true || status["pinned_origins"] != float64(1) {
		t.Fatalf("unexpected trust status: %#v", status)
	}
}
