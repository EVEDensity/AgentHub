// Package crypto provides secret management and data masking utilities for
// the platform. AES-256-GCM is used for encrypting API keys and other secrets
// at rest; the data masking functions redact PII before it hits audit logs.
package crypto

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"io"
	"regexp"
	"strings"
)

// ── AES-256-GCM encryption ──────────────────────────────────────────────

// Encrypt encrypts plaintext using AES-256-GCM with a random 12-byte nonce.
// The result is base64(nonce || ciphertext). The key must be exactly 32 bytes.
// If it is shorter it is padded with zeros; if longer it is truncated.
func Encrypt(plaintext string, key []byte) (string, error) {
	block, err := aes.NewCipher(padKey(key))
	if err != nil {
		return "", fmt.Errorf("create cipher: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", fmt.Errorf("create gcm: %w", err)
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return "", fmt.Errorf("generate nonce: %w", err)
	}
	sealed := gcm.Seal(nonce, nonce, []byte(plaintext), nil)
	return base64.StdEncoding.EncodeToString(sealed), nil
}

// Decrypt reverses Encrypt. The ciphertext must be the base64-encoded output
// of Encrypt (nonce || ciphertext).
func Decrypt(encoded string, key []byte) (string, error) {
	sealed, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return "", fmt.Errorf("decode ciphertext: %w", err)
	}
	block, err := aes.NewCipher(padKey(key))
	if err != nil {
		return "", fmt.Errorf("create cipher: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", fmt.Errorf("create gcm: %w", err)
	}
	nonceSize := gcm.NonceSize()
	if len(sealed) < nonceSize {
		return "", fmt.Errorf("ciphertext too short (%d bytes, need >=%d)", len(sealed), nonceSize)
	}
	nonce, ciphertext := sealed[:nonceSize], sealed[nonceSize:]
	plain, err := gcm.Open(nil, nonce, ciphertext, nil)
	if err != nil {
		return "", fmt.Errorf("decrypt: %w", err)
	}
	return string(plain), nil
}

// GenerateKey returns a new random 32-byte (AES-256) key encoded as hex.
// Use this to generate the KMS_MASTER_KEY env var.
func GenerateKey() (string, error) {
	key := make([]byte, 32)
	if _, err := io.ReadFull(rand.Reader, key); err != nil {
		return "", err
	}
	return hex.EncodeToString(key), nil
}

// padKey ensures the key is exactly 32 bytes. Shorter keys are right-padded
// with zeros; longer keys are truncated. Production deployments must use
// exactly 32-byte keys.
func padKey(key []byte) []byte {
	if len(key) == 32 {
		return key
	}
	out := make([]byte, 32)
	copy(out, key)
	return out
}

// ── Data masking ────────────────────────────────────────────────────────

var (
	// Matches common API key formats: sk-..., sk-ant-..., etc.
	apiKeyPattern = regexp.MustCompile(`(sk-[a-zA-Z0-9_-]{20,})`)
	// Matches email addresses (basic).
	emailPattern = regexp.MustCompile(`([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})`)
	// Matches bearer tokens in headers.
	bearerTokenPattern = regexp.MustCompile(`(?i)(bearer\s+)([a-zA-Z0-9._-]{20,})`)
)

// MaskAPIKey replaces API keys in a string with the format "sk-...XXXX"
// where XXXX are the last 4 chars of the key.
func MaskAPIKey(s string) string {
	return apiKeyPattern.ReplaceAllStringFunc(s, func(match string) string {
		if len(match) > 8 {
			return match[:5] + "..." + match[len(match)-4:]
		}
		return "sk-...****"
	})
}

// MaskEmail replaces email addresses with "u***@domain".
func MaskEmail(s string) string {
	return emailPattern.ReplaceAllStringFunc(s, func(match string) string {
		parts := strings.SplitN(match, "@", 2)
		if len(parts) == 2 && len(parts[0]) > 1 {
			return parts[0][:1] + "***@" + parts[1]
		}
		return "***@***"
	})
}

// MaskBearerToken replaces bearer tokens with "Bearer tok...XXXX".
func MaskBearerToken(s string) string {
	return bearerTokenPattern.ReplaceAllStringFunc(s, func(match string) string {
		parts := bearerTokenPattern.FindStringSubmatch(match)
		if len(parts) >= 3 && len(parts[2]) > 8 {
			return parts[1] + "tok..." + parts[2][len(parts[2])-4:]
		}
		return "Bearer tok...****"
	})
}

// MaskPII applies all masking rules to a string. Safe to call on any
// log message, payload, or error string before it hits audit streams.
func MaskPII(s string) string {
	s = MaskAPIKey(s)
	s = MaskEmail(s)
	s = MaskBearerToken(s)
	return s
}

// MaskMap recursively walks a map[string]any and applies MaskPII to every
// string value. Useful for sanitising envelope payloads before audit.
func MaskMap(m map[string]any) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		switch vv := v.(type) {
		case string:
			out[k] = MaskPII(vv)
		case map[string]any:
			out[k] = MaskMap(vv)
		case []any:
			masked := make([]any, len(vv))
			for i, item := range vv {
				if s, ok := item.(string); ok {
					masked[i] = MaskPII(s)
				} else {
					masked[i] = item
				}
			}
			out[k] = masked
		default:
			out[k] = v
		}
	}
	return out
}
