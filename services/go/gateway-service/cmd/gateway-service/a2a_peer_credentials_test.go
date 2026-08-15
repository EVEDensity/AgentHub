package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestA2APeerCredentialsFromEnvLoadsOriginBoundToken(t *testing.T) {
	tokenPath := filepath.Join(t.TempDir(), "peer-token")
	if err := os.WriteFile(tokenPath, []byte("peer-secret\n"), 0o600); err != nil {
		t.Fatalf("write peer token: %v", err)
	}
	configured, err := json.Marshal(map[string]string{"HTTPS://Peer.Example": tokenPath})
	if err != nil {
		t.Fatalf("encode peer credential config: %v", err)
	}
	t.Setenv("A2A_PEER_BEARER_TOKEN_FILES_JSON", string(configured))

	credentials, err := a2aPeerCredentialsFromEnv()
	if err != nil {
		t.Fatalf("load peer credentials: %v", err)
	}
	token, found, err := credentials.bearerFor("https://peer.example/a2a")
	if err != nil || !found || token != "peer-secret" {
		t.Fatalf("resolve canonical peer credential: token=%q found=%v err=%v", token, found, err)
	}
	if credentials.Count() != 1 {
		t.Fatalf("expected one configured peer origin, got %d", credentials.Count())
	}
	serialized, err := json.Marshal(credentials)
	if err != nil {
		t.Fatalf("serialize redacted credential container: %v", err)
	}
	if string(serialized) != "{}" || strings.Contains(string(serialized), token) {
		t.Fatalf("peer credential container exposed secret material: %s", serialized)
	}
}

func TestA2APeerCredentialsRejectsMultilineToken(t *testing.T) {
	tokenPath := filepath.Join(t.TempDir(), "peer-token")
	if err := os.WriteFile(tokenPath, []byte("first\nsecond\n"), 0o600); err != nil {
		t.Fatalf("write peer token: %v", err)
	}
	configured, err := json.Marshal(map[string]string{"https://peer.example": tokenPath})
	if err != nil {
		t.Fatalf("encode peer credential config: %v", err)
	}
	t.Setenv("A2A_PEER_BEARER_TOKEN_FILES_JSON", string(configured))

	if _, err := a2aPeerCredentialsFromEnv(); err == nil || !strings.Contains(err.Error(), "single-line") {
		t.Fatalf("expected multiline peer token rejection, got %v", err)
	}
}

func TestA2APeerCredentialsRejectsTrailingJSON(t *testing.T) {
	t.Setenv("A2A_PEER_BEARER_TOKEN_FILES_JSON", "{} {}")
	if _, err := a2aPeerCredentialsFromEnv(); err == nil || !strings.Contains(err.Error(), "exactly one JSON object") {
		t.Fatalf("expected trailing JSON rejection, got %v", err)
	}
}
