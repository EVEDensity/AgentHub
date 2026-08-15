package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAgentHubToAgentHubStrictPinnedInterop(t *testing.T) {
	senderServer := httptest.NewUnstartedServer(nil)
	receiverServer := httptest.NewUnstartedServer(nil)
	t.Cleanup(senderServer.Close)
	t.Cleanup(receiverServer.Close)
	senderURL := "http://" + senderServer.Listener.Addr().String()
	receiverURL := "http://" + receiverServer.Listener.Addr().String()

	senderPublicKey, senderPrivateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("generate sender signing key: %v", err)
	}
	receiverPublicKey, receiverPrivateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("generate receiver signing key: %v", err)
	}
	senderControl := newFakeA2AControlPlane()
	receiverControl := newFakeA2AControlPlane()
	const senderIssuedToken = "sender-issued-peer-token"
	const receiverIssuedToken = "receiver-issued-peer-token"

	senderA2A, err := newA2AHandlerWithTrustPolicy(
		senderURL,
		nil,
		&A2ATLSConfig{},
		strictPinnedPolicy(receiverURL, receiverPublicKey),
		&ed25519A2ACardSigner{privateKey: senderPrivateKey},
		&A2APeerCredentials{bearerByOrigin: map[string]string{receiverURL: receiverIssuedToken}},
		senderControl,
	)
	if err != nil {
		t.Fatalf("construct sender A2A handler: %v", err)
	}
	receiverA2A, err := newA2AHandlerWithTrustPolicy(
		receiverURL,
		nil,
		&A2ATLSConfig{},
		strictPinnedPolicy(senderURL, senderPublicKey),
		&ed25519A2ACardSigner{privateKey: receiverPrivateKey},
		&A2APeerCredentials{bearerByOrigin: map[string]string{senderURL: senderIssuedToken}},
		receiverControl,
	)
	if err != nil {
		t.Fatalf("construct receiver A2A handler: %v", err)
	}
	senderServer.Config.Handler = mountA2AForInterop(senderA2A)
	receiverServer.Config.Handler = mountA2AForInterop(receiverA2A)
	senderServer.Start()
	receiverServer.Start()
	if senderServer.URL != senderURL || receiverServer.URL != receiverURL {
		t.Fatalf("unexpected interop server origins: sender=%s receiver=%s", senderServer.URL, receiverServer.URL)
	}

	recorder, response := callA2ATaskAPI(t, senderA2A, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params: map[string]any{
			"id":                   "interop-task",
			"workspaceId":          "workspace-1",
			"agentUrl":             receiverURL,
			"requiredCapabilities": []any{"rag"},
			"message":              map[string]any{"role": "user", "parts": []any{map[string]any{"type": "text", "text": "perform trusted work"}}},
		},
		ID: "interop-send",
	}, "Bearer local-user-token")
	if recorder.Code != http.StatusOK || response.Error != nil || response.Result.(map[string]any)["status"] != "submitted" {
		t.Fatalf("expected strict-pinned interop submit, status=%d response=%#v", recorder.Code, response)
	}
	if len(senderControl.submits) != 1 || len(receiverControl.accepts) != 1 {
		t.Fatalf("expected one outbound submit and one inbound accept, sender=%#v receiver=%#v", senderControl.submits, receiverControl.accepts)
	}
	if len(receiverControl.submits) != 0 {
		t.Fatalf("inbound task recursively entered outbound delegation: %#v", receiverControl.submits)
	}
	if receiverControl.accepts[0].SourceAgentURL != senderURL {
		t.Fatalf("receiver did not bind the signed source Agent URL: %#v", receiverControl.accepts[0])
	}
	if len(receiverControl.authorities) != 1 || receiverControl.authorities[0] != "Bearer "+receiverIssuedToken {
		t.Fatalf("receiver control plane did not receive its own peer credential: %#v", receiverControl.authorities)
	}

	cancelRecorder, cancelResponse := callA2ATaskAPI(t, senderA2A, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/cancel",
		Params:  map[string]any{"id": "interop-task", "workspaceId": "workspace-1", "agentUrl": "https://must-not-forward.test"},
		ID:      "interop-cancel",
	}, "Bearer local-user-token")
	if cancelRecorder.Code != http.StatusOK || cancelResponse.Error != nil || cancelResponse.Result.(map[string]any)["status"] != "canceled" {
		t.Fatalf("expected end-to-end cancellation, status=%d response=%#v", cancelRecorder.Code, cancelResponse)
	}
	if receiverControl.tasks["interop-task"].State != "canceled" {
		t.Fatalf("receiver inbound Mission was not canceled: %#v", receiverControl.tasks["interop-task"])
	}
	if len(receiverControl.authorities) != 2 || receiverControl.authorities[1] != "Bearer "+receiverIssuedToken {
		t.Fatalf("remote cancellation did not use receiver-issued auth: %#v", receiverControl.authorities)
	}

	receiverControl.acceptErr = &a2aControlPlaneError{StatusCode: http.StatusServiceUnavailable, Detail: "receiver unavailable"}
	failureRecorder, failureResponse := callA2ATaskAPI(t, senderA2A, A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  "tasks/send",
		Params: map[string]any{
			"id":          "interop-failure",
			"workspaceId": "workspace-1",
			"agentUrl":    receiverURL,
			"message":     map[string]any{"role": "user", "parts": []any{map[string]any{"type": "text", "text": "record remote failure"}}},
		},
		ID: "interop-failure-send",
	}, "Bearer local-user-token")
	if failureRecorder.Code != http.StatusOK || failureResponse.Error != nil || failureResponse.Result.(map[string]any)["status"] != "failed" {
		t.Fatalf("expected durable sender failure projection, status=%d response=%#v", failureRecorder.Code, failureResponse)
	}
	if len(senderControl.fails) != 1 || !strings.Contains(senderControl.fails[0], "remote A2A error") {
		t.Fatalf("remote protocol failure was not written back to sender Mission: %#v", senderControl.fails)
	}
}

func strictPinnedPolicy(origin string, publicKey ed25519.PublicKey) A2ATrustPolicy {
	return A2ATrustPolicy{
		RequirePinnedKey: true,
		TrustedKeys: map[string]map[string]struct{}{
			origin: {hex.EncodeToString(publicKey): {}},
		},
	}
}

func mountA2AForInterop(handler http.Handler) http.Handler {
	mux := http.NewServeMux()
	mux.Handle("/.well-known/agent-card.json", handler)
	mux.Handle("/platform/a2a/", http.StripPrefix("/platform/a2a", handler))
	return mux
}
