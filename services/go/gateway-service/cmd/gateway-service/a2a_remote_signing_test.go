package main

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeTestA2ASignerToken(t *testing.T, token string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "a2a-signer-token")
	if err := os.WriteFile(path, []byte(token+"\n"), 0o600); err != nil {
		t.Fatalf("write signer token: %v", err)
	}
	return path
}

func newTestA2ARemoteSignerServer(t *testing.T, privateKey ed25519.PrivateKey, corruptSignature bool) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("unexpected signer method %s", r.Method)
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if r.Header.Get("Authorization") != "Bearer signer-token" {
			t.Errorf("unexpected signer authorization header")
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		var request a2aRemoteSignerRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Errorf("decode signer request: %v", err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		if request.Purpose != a2aRemoteSignerPurpose {
			t.Errorf("unexpected signer purpose %q", request.Purpose)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		response := a2aRemoteSignerResponse{
			Algorithm:  "ed25519",
			KeyID:      "agenthub-card",
			KeyVersion: "2026-08-15",
		}
		switch request.Operation {
		case "public_key":
			response.PublicKey = hex.EncodeToString(privateKey.Public().(ed25519.PublicKey))
		case "sign":
			if request.KeyVersion != response.KeyVersion {
				t.Errorf("unexpected signer key version %q", request.KeyVersion)
				w.WriteHeader(http.StatusConflict)
				return
			}
			payload, err := base64.StdEncoding.DecodeString(request.Payload)
			if err != nil {
				t.Errorf("decode signer payload: %v", err)
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			if corruptSignature {
				payload = []byte("different-payload")
			}
			response.Signature = hex.EncodeToString(ed25519.Sign(privateKey, payload))
		default:
			t.Errorf("unexpected signer operation %q", request.Operation)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(response); err != nil {
			t.Errorf("encode signer response: %v", err)
		}
	}))
}

func TestA2ACardSignerFromEnvRejectsConflictingBackends(t *testing.T) {
	t.Setenv("A2A_CARD_SIGNING_KEY_FILE", "local-key")
	t.Setenv("A2A_CARD_SIGNER_URL", "https://signer.example.com/v1/sign")
	if _, err := a2aCardSignerFromEnv(); err == nil || !strings.Contains(err.Error(), "mutually exclusive") {
		t.Fatalf("expected conflicting signer backends to fail, got %v", err)
	}
}

func TestRemoteA2ACardSignerRequiresHTTPSUnlessLoopbackIsExplicit(t *testing.T) {
	if _, err := newRemoteA2ACardSigner("http://127.0.0.1:9000/v1/sign", "key", "token", false, nil); err == nil || !strings.Contains(err.Error(), "requires HTTPS") {
		t.Fatalf("expected insecure signer rejection, got %v", err)
	}
	if _, err := newRemoteA2ACardSigner("http://signer.internal/v1/sign", "key", "token", true, nil); err == nil || !strings.Contains(err.Error(), "requires HTTPS") {
		t.Fatalf("expected non-loopback HTTP signer rejection, got %v", err)
	}
	if _, err := newRemoteA2ACardSigner("http://localhost:9000/v1/sign", "key", "token", true, nil); err != nil {
		t.Fatalf("expected explicit loopback development signer, got %v", err)
	}
}

func TestRemoteA2ACardSignerSignsWithoutExportingPrivateKey(t *testing.T) {
	seed := make([]byte, ed25519.SeedSize)
	for index := range seed {
		seed[index] = byte(index + 17)
	}
	privateKey := ed25519.NewKeyFromSeed(seed)
	server := newTestA2ARemoteSignerServer(t, privateKey, false)
	defer server.Close()

	signer, err := newRemoteA2ACardSigner(server.URL, "agenthub-card", "signer-token", true, nil)
	if err != nil {
		t.Fatalf("create remote signer: %v", err)
	}
	card := buildAgentHubCard("https://agenthub.test")
	if err := SignAgentCard(context.Background(), card, signer); err != nil {
		t.Fatalf("sign AgentHub card remotely: %v", err)
	}
	if card.Security == nil || card.Security.KeyID != "agenthub-card" || card.Security.KeyVersion != "2026-08-15" {
		t.Fatalf("expected public key identity metadata, got %#v", card.Security)
	}
	if err := VerifyAgentCardSignature(card); err != nil {
		t.Fatalf("verify remotely signed card: %v", err)
	}
	encoded, err := json.Marshal(card)
	if err != nil {
		t.Fatalf("marshal remotely signed card: %v", err)
	}
	if strings.Contains(string(encoded), hex.EncodeToString(seed)) || strings.Contains(string(encoded), "signer-token") {
		t.Fatal("Agent Card exposed remote signer credential or private seed")
	}
}

func TestRemoteA2ACardSignerRejectsMismatchedSignature(t *testing.T) {
	privateKey := ed25519.NewKeyFromSeed(make([]byte, ed25519.SeedSize))
	server := newTestA2ARemoteSignerServer(t, privateKey, true)
	defer server.Close()
	signer, err := newRemoteA2ACardSigner(server.URL, "agenthub-card", "signer-token", true, nil)
	if err != nil {
		t.Fatalf("create remote signer: %v", err)
	}
	card := buildAgentHubCard("https://agenthub.test")
	if err := SignAgentCard(context.Background(), card, signer); err == nil || !strings.Contains(err.Error(), "does not match") {
		t.Fatalf("expected mismatched remote signature rejection, got %v", err)
	}
	if card.Security != nil || card.Signature != "" {
		t.Fatalf("failed signing mutated the published card: %#v", card)
	}
}

func TestA2ACardSignerFromEnvLoadsRemoteSignerTokenFile(t *testing.T) {
	privateKey := ed25519.NewKeyFromSeed(make([]byte, ed25519.SeedSize))
	server := newTestA2ARemoteSignerServer(t, privateKey, false)
	defer server.Close()
	t.Setenv("A2A_CARD_SIGNING_KEY_FILE", "")
	t.Setenv("A2A_CARD_SIGNER_URL", server.URL)
	t.Setenv("A2A_CARD_SIGNER_KEY_ID", "agenthub-card")
	t.Setenv("A2A_CARD_SIGNER_TOKEN_FILE", writeTestA2ASignerToken(t, "signer-token"))
	t.Setenv("A2A_CARD_SIGNER_ALLOW_INSECURE_HTTP", "true")

	signer, err := a2aCardSignerFromEnv()
	if err != nil {
		t.Fatalf("load remote signer from environment: %v", err)
	}
	card := buildAgentHubCard("https://agenthub.test")
	if err := SignAgentCard(context.Background(), card, signer); err != nil {
		t.Fatalf("sign with environment-configured remote signer: %v", err)
	}
}

func TestRemoteA2ACardSignerDoesNotFollowRedirects(t *testing.T) {
	targetCalled := false
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		targetCalled = true
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{}`))
	}))
	defer target.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer redirect.Close()
	signer, err := newRemoteA2ACardSigner(redirect.URL, "agenthub-card", "signer-token", true, nil)
	if err != nil {
		t.Fatalf("create remote signer: %v", err)
	}
	if _, err := signer.Identity(context.Background()); err == nil || !strings.Contains(err.Error(), "HTTP 307") {
		t.Fatalf("expected signer redirect rejection, got %v", err)
	}
	if targetCalled {
		t.Fatal("remote signer followed a redirect")
	}
}
