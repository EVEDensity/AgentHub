package crypto

import (
	"encoding/hex"
	"strings"
	"testing"
)

// ── AES-256-GCM Encrypt/Decrypt ───────────────────────────────────────

func TestEncryptDecryptRoundTrip(t *testing.T) {
	key := make([]byte, 32)
	copy(key, []byte("test-secret-key-32-bytes-xxxxx"))

	plaintext := "Hello, world! This is a secret API key: sk-abc123def456"

	encrypted, err := Encrypt(plaintext, key)
	if err != nil {
		t.Fatalf("encrypt: %v", err)
	}
	if encrypted == "" {
		t.Fatal("encrypted output must not be empty")
	}
	if encrypted == plaintext {
		t.Fatal("encrypted text should differ from plaintext")
	}

	decrypted, err := Decrypt(encrypted, key)
	if err != nil {
		t.Fatalf("decrypt: %v", err)
	}
	if decrypted != plaintext {
		t.Fatalf("round-trip mismatch: got '%s', want '%s'", decrypted, plaintext)
	}
}

func TestDecryptWrongKey(t *testing.T) {
	k1 := make([]byte, 32)
	k2 := make([]byte, 32)
	copy(k1, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
	copy(k2, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

	enc, _ := Encrypt("secret", k1)
	_, err := Decrypt(enc, k2)
	if err == nil {
		t.Fatal("decrypt with wrong key should fail")
	}
}

func TestDecryptCorrupted(t *testing.T) {
	key := make([]byte, 32)
	copy(key, "secret-key-xxxxxxxxxxxxxxxxxxxxxxx")

	_, err := Decrypt("not-valid-base64!!!", key)
	if err == nil {
		t.Fatal("corrupted ciphertext should fail")
	}

	_, err = Decrypt("dG9vLXNob3J0", key) // too short to contain nonce+data
	if err == nil {
		t.Fatal("short ciphertext should fail")
	}
}

func TestEncryptUniqueNonce(t *testing.T) {
	key := make([]byte, 32)
	copy(key, "unique-nonce-test-key-32bytesxx")

	e1, _ := Encrypt("hello", key)
	e2, _ := Encrypt("hello", key)
	if e1 == e2 {
		t.Fatal("two encryptions of same plaintext should differ (random nonce)")
	}
}

func TestPadKey(t *testing.T) {
	// Exact 32 bytes
	k32 := []byte("abcdefghijklmnopqrstuvwxyz012345")
	if got := padKey(k32); len(got) != 32 || string(got) != string(k32) {
		t.Fatalf("32-byte key should be unchanged, got %d bytes", len(got))
	}

	// Shorter than 32
	k16 := []byte("0123456789abcdef")
	if got := padKey(k16); len(got) != 32 {
		t.Fatalf("short key should be padded to 32, got %d", len(got))
	}

	// Longer than 32
	k64 := make([]byte, 64)
	if got := padKey(k64); len(got) != 32 {
		t.Fatalf("long key should be truncated to 32, got %d", len(got))
	}
}

func TestGenerateKey(t *testing.T) {
	k1, err := GenerateKey()
	if err != nil {
		t.Fatalf("generate key: %v", err)
	}
	k2, _ := GenerateKey()
	if k1 == k2 {
		t.Fatal("two generated keys should differ")
	}

	// Should be valid hex and 64 chars (32 bytes → 64 hex chars)
	if len(k1) != 64 {
		t.Fatalf("generated key should be 64 hex chars, got %d", len(k1))
	}
	if _, err := hex.DecodeString(k1); err != nil {
		t.Fatalf("generated key should be valid hex: %v", err)
	}
}

// ── Data Masking ─────────────────────────────────────────────────────

func TestMaskAPIKey(t *testing.T) {
	cases := []struct {
		input    string
		expected string
	}{
		{"sk-abc123def456ghijklmnopqrstuv", "sk-ab...stuv"},
		{"my key is sk-abcdefghijklmnopqrstuvwx in text", "my key is sk-ab...uvwx in text"},
		{"sk-short", "sk-short"},
		{"no api key here", "no api key here"},
		{"sk-ant-verylongkey1234567890abcdefg", "sk-an...defg"},
	}

	for _, c := range cases {
		got := MaskAPIKey(c.input)
		if got != c.expected {
			t.Errorf("MaskAPIKey(%q) = %q, want %q", c.input, got, c.expected)
		}
	}
}

func TestMaskEmail(t *testing.T) {
	cases := []struct {
		input    string
		expected string
	}{
		{"user@example.com", "u***@example.com"},
		{"a@b.c", "a@b.c"},
		{"contact admin@company.org for help", "contact a***@company.org for help"},
		{"no email", "no email"},
	}

	for _, c := range cases {
		got := MaskEmail(c.input)
		if got != c.expected {
			t.Errorf("MaskEmail(%q) = %q, want %q", c.input, got, c.expected)
		}
	}
}

func TestMaskBearerToken(t *testing.T) {
	cases := []struct {
		input    string
		expected string
	}{
		{"Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "Authorization: Bearer tok...wxyz"},
		{"bearer 1234567890abcdefghij", "bearer tok...ghij"},
		{"Bearer short", "Bearer short"},
		{"no token", "no token"},
	}

	for _, c := range cases {
		got := MaskBearerToken(c.input)
		if got != c.expected {
			t.Errorf("MaskBearerToken(%q) = %q, want %q", c.input, got, c.expected)
		}
	}
}

func TestMaskPII(t *testing.T) {
	input := "User user@example.com API sk-abcdef1234567890ghijkl Bearer abcdefghijklmnopqrstuvwxyz"
	got := MaskPII(input)

	if strings.Contains(got, "user@example.com") {
		t.Error("email should be masked")
	}
	if strings.Contains(got, "sk-abcdef1234567890ghijkl") {
		t.Error("API key should be masked")
	}
	if strings.Contains(got, "abcdefghijklmnopqrstuvwxyz") {
		t.Error("bearer token should be masked")
	}
}

func TestMaskMap(t *testing.T) {
	input := map[string]any{
		"user":    "alice@example.com",
		"api_key": "sk-secret-key-12345678-abc",
		"header":  "Bearer mysecrettoken12345",
		"count":   42,
		"nested": map[string]any{
			"email": "bob@test.org",
		},
		"items": []any{"item1", "item2"},
	}

	output := MaskMap(input)

	// Strings should be masked
	if s, ok := output["user"].(string); ok && s == "alice@example.com" {
		t.Error("user email should be masked in map")
	}
	// Numbers should be unchanged
	if v, ok := output["count"].(int); ok && v != 42 {
		t.Error("int values should not be modified")
	}
	// Nested maps should be masked
	if nested, ok := output["nested"].(map[string]any); ok {
		if e, ok := nested["email"].(string); ok && e == "bob@test.org" {
			t.Error("nested email should be masked")
		}
	}
}
