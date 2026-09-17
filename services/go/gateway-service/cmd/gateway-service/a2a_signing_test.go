package main

import (
	"context"
	"crypto/ed25519"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeTestA2ASigningKey(t *testing.T, key []byte) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "a2a-signing-key.hex")
	if err := os.WriteFile(path, []byte(hex.EncodeToString(key)), 0o600); err != nil {
		t.Fatalf("write A2A signing key: %v", err)
	}
	return path
}

func clearRemoteA2ASignerEnv(t *testing.T) {
	t.Helper()
	t.Setenv("A2A_CARD_SIGNER_URL", "")
	t.Setenv("A2A_CARD_SIGNER_KEY_ID", "")
	t.Setenv("A2A_CARD_SIGNER_TOKEN_FILE", "")
	t.Setenv("A2A_CARD_SIGNER_ALLOW_INSECURE_HTTP", "")
}

func TestA2ACardSignerFromEnvReturnsNilWithoutKeyFile(t *testing.T) {
	clearRemoteA2ASignerEnv(t)
	t.Setenv("A2A_CARD_SIGNING_KEY_FILE", "")
	signer, err := a2aCardSignerFromEnv()
	if err != nil || signer != nil {
		t.Fatalf("expected optional signer to be absent, signer=%#v err=%v", signer, err)
	}
}

func TestA2ARequireSignedSelfCardFromEnvIsStrict(t *testing.T) {
	t.Setenv("A2A_REQUIRE_SIGNED_SELF_CARD", "true")
	required, err := a2aRequireSignedSelfCardFromEnv()
	if err != nil || !required {
		t.Fatalf("expected signed self-card requirement, required=%v err=%v", required, err)
	}
	t.Setenv("A2A_REQUIRE_SIGNED_SELF_CARD", "sometimes")
	if _, err := a2aRequireSignedSelfCardFromEnv(); err == nil || !strings.Contains(err.Error(), "must be a boolean") {
		t.Fatalf("expected invalid signing requirement rejection, got %v", err)
	}
}

func TestA2ACardSignerFromEnvLoadsSeedAndSignsCard(t *testing.T) {
	clearRemoteA2ASignerEnv(t)
	seed := make([]byte, ed25519.SeedSize)
	for index := range seed {
		seed[index] = byte(index + 1)
	}
	keyPath := writeTestA2ASigningKey(t, seed)
	t.Setenv("A2A_CARD_SIGNING_KEY_FILE", keyPath)

	signer, err := a2aCardSignerFromEnv()
	if err != nil {
		t.Fatalf("load A2A signer: %v", err)
	}
	card := buildAgentHubCard("https://agenthub.test")
	if err := SignAgentCard(context.Background(), card, signer); err != nil {
		t.Fatalf("sign AgentHub card: %v", err)
	}
	if card.Signature == "" || card.Security == nil || card.Security.KeyAlgorithm != "ed25519" {
		t.Fatalf("expected signed AgentHub card, got %#v", card.Security)
	}
	if err := VerifyAgentCardSignature(card); err != nil {
		t.Fatalf("verify signed AgentHub card: %v", err)
	}
	encoded, err := json.Marshal(card)
	if err != nil {
		t.Fatalf("marshal signed AgentHub card: %v", err)
	}
	if strings.Contains(string(encoded), hex.EncodeToString(seed)) {
		t.Fatal("Agent Card exposed private signing seed")
	}
}

func TestA2ACardSignerFromEnvRejectsInvalidPrivateKey(t *testing.T) {
	clearRemoteA2ASignerEnv(t)
	keyPath := filepath.Join(t.TempDir(), "invalid-a2a-signing-key.hex")
	if err := os.WriteFile(keyPath, []byte("not-a-private-key"), 0o600); err != nil {
		t.Fatalf("write invalid signing key: %v", err)
	}
	t.Setenv("A2A_CARD_SIGNING_KEY_FILE", keyPath)

	if _, err := a2aCardSignerFromEnv(); err == nil || !strings.Contains(err.Error(), "hex-encoded") {
		t.Fatalf("expected invalid signing key rejection, got %v", err)
	}
}

func TestSignedAgentHubCardCanBePinnedByStrictPeer(t *testing.T) {
	seed := make([]byte, ed25519.SeedSize)
	for index := range seed {
		seed[index] = byte(255 - index)
	}
	signer := &ed25519A2ACardSigner{privateKey: ed25519.NewKeyFromSeed(seed)}
	handler := newTestA2AHandlerWithPolicy(A2ATrustPolicy{}, signer, newFakeA2AControlPlane())

	cardRequest := httptest.NewRequest(http.MethodGet, "/.well-known/agent-card.json", nil)
	cardRecorder := httptest.NewRecorder()
	handler.ServeHTTP(cardRecorder, cardRequest)
	if cardRecorder.Code != http.StatusOK {
		t.Fatalf("expected AgentHub card, status=%d body=%s", cardRecorder.Code, cardRecorder.Body.String())
	}
	var card AgentCard
	if err := json.Unmarshal(cardRecorder.Body.Bytes(), &card); err != nil {
		t.Fatalf("decode AgentHub card: %v", err)
	}
	if card.Security == nil || card.Signature == "" {
		t.Fatalf("expected published AgentHub card to be signed: %#v", card)
	}
	strictPeerPolicy := A2ATrustPolicy{
		RequirePinnedKey: true,
		TrustedKeys: map[string]map[string]struct{}{
			"http://agenthub.test": {card.Security.PublicKey: {}},
		},
	}
	if err := VerifyAgentCardTrust(&card, card.URL, strictPeerPolicy); err != nil {
		t.Fatalf("strict peer rejected pinned AgentHub card: %v", err)
	}

	statusRequest := httptest.NewRequest(http.MethodGet, "/trust-status", nil)
	statusRecorder := httptest.NewRecorder()
	handler.ServeHTTP(statusRecorder, statusRequest)
	if statusRecorder.Code != http.StatusOK || !strings.Contains(statusRecorder.Body.String(), `"self_card_signed":true`) {
		t.Fatalf("expected signed-card status without key material, status=%d body=%s", statusRecorder.Code, statusRecorder.Body.String())
	}
	if strings.Contains(statusRecorder.Body.String(), card.Security.PublicKey) {
		t.Fatalf("trust status exposed AgentHub public key: %s", statusRecorder.Body.String())
	}
}
