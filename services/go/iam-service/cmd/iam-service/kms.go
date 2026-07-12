package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"time"

	"github.com/agenthub/platform/shared/crypto"
	"github.com/agenthub/platform/shared/db"
)

// ── KMS (Key Management Service) ───────────────────────────────────────

// getMasterKey reads the KMS master key from the environment. In production
// this should come from a hardware security module or cloud KMS; for now we
// accept a hex-encoded 32-byte key via env.
func getMasterKey() []byte {
	hexKey := getenv("KMS_MASTER_KEY", "")
	if hexKey == "" {
		return nil
	}
	key, err := hex.DecodeString(hexKey)
	if err != nil || len(key) < 16 {
		return nil
	}
	return key
}

// rotateKey generates a new master key, re-encrypts all stored secrets with
// it, and updates the KMS_MASTER_KEY env var reference. In a real deployment
// this would write to a secrets manager; here we log the new key so the
// operator can update the env var.
func rotateKeyAndReencrypt(ctx context.Context, pool *db.Pool, oldKey, newKey []byte) (int, error) {
	if oldKey == nil || newKey == nil {
		return 0, nil
	}
	rows, err := pool.Query(ctx, `SELECT id, encrypted_secret FROM platform_secrets`)
	if err != nil {
		return 0, err
	}
	defer rows.Close()

	type secretRow struct {
		id      string
		encData string
	}
	var secrets []secretRow
	for rows.Next() {
		var sr secretRow
		if err := rows.Scan(&sr.id, &sr.encData); err == nil {
			secrets = append(secrets, sr)
		}
	}
	if len(secrets) == 0 {
		return 0, nil
	}

	count := 0
	for _, sr := range secrets {
		plain, err := crypto.Decrypt(sr.encData, oldKey)
		if err != nil {
			continue
		}
		reEnc, err := crypto.Encrypt(plain, newKey)
		if err != nil {
			continue
		}
		_, _ = pool.Exec(ctx, `UPDATE platform_secrets SET encrypted_secret=$1, rotated_at=now() WHERE id=$2`, reEnc, sr.id)
		count++
	}
	return count, nil
}

// ── HTTP handlers ──────────────────────────────────────────────────────

// serveKMS handles /iam/secrets — encrypt/decrypt/list secrets.
func serveKMS(pool *db.Pool, w http.ResponseWriter, r *http.Request, key []byte) {
	w.Header().Set("Content-Type", "application/json")
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	switch r.Method {
	case http.MethodGet:
		// GET /iam/secrets — list all stored secret metadata (not plaintext).
		rows, err := pool.Query(ctx, `SELECT id, name, tenant_id, provider, created_at, rotated_at FROM platform_secrets ORDER BY created_at DESC LIMIT 100`)
		if err != nil {
			jsonError(w, http.StatusBadGateway, err.Error())
			return
		}
		defer rows.Close()
		secrets := []map[string]any{}
		for rows.Next() {
			var id, name, tid, provider string
			var created, rotated time.Time
			if err := rows.Scan(&id, &name, &tid, &provider, &created, &rotated); err == nil {
				secrets = append(secrets, map[string]any{
					"id": id, "name": name, "tenant_id": tid, "provider": provider,
					"created_at": created.Format(time.RFC3339), "rotated_at": rotated.Format(time.RFC3339),
				})
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"count": len(secrets), "secrets": secrets})

	case http.MethodPost:
		// POST /iam/secrets — store a new secret (API key, etc.).
		if key == nil {
			jsonError(w, http.StatusServiceUnavailable, "KMS_MASTER_KEY not configured — cannot encrypt secrets")
			return
		}
		var req struct {
			Name     string `json:"name"`
			TenantID string `json:"tenant_id"`
			Provider string `json:"provider"`
			Secret   string `json:"secret"` // plaintext — encrypted before storage
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, http.StatusBadRequest, "invalid json body")
			return
		}
		if req.Name == "" || req.Secret == "" {
			jsonError(w, http.StatusBadRequest, "name and secret are required")
			return
		}
		encrypted, err := crypto.Encrypt(req.Secret, key)
		if err != nil {
			jsonError(w, http.StatusInternalServerError, "encrypt failed: "+err.Error())
			return
		}
		id := "sec-" + randHex(16)
		_, err = pool.Exec(ctx, `INSERT INTO platform_secrets (id, name, tenant_id, provider, encrypted_secret) VALUES ($1,$2,$3,$4,$5)`,
			id, req.Name, req.TenantID, req.Provider, encrypted)
		if err != nil {
			jsonError(w, http.StatusBadGateway, "store failed: "+err.Error())
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "id": id, "name": req.Name})

	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// serveKMSReveal handles POST /iam/secrets/reveal — decrypt a stored secret.
// This endpoint must be protected by authz (tenant_admin scope required).
func serveKMSReveal(pool *db.Pool, w http.ResponseWriter, r *http.Request, key []byte) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")

	if key == nil {
		jsonError(w, http.StatusServiceUnavailable, "KMS_MASTER_KEY not configured")
		return
	}

	var req struct {
		ID string `json:"id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, http.StatusBadRequest, "invalid json body")
		return
	}
	if req.ID == "" {
		jsonError(w, http.StatusBadRequest, "id is required")
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	var encrypted string
	err := pool.QueryRow(ctx, `SELECT encrypted_secret FROM platform_secrets WHERE id=$1`, req.ID).Scan(&encrypted)
	if err != nil {
		jsonError(w, http.StatusNotFound, "secret not found")
		return
	}
	plain, err := crypto.Decrypt(encrypted, key)
	if err != nil {
		jsonError(w, http.StatusInternalServerError, "decrypt failed: "+err.Error())
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]any{"id": req.ID, "secret": plain})
}

// serveKMSRotate handles POST /iam/secrets/rotate — generate a new key,
// re-encrypt all secrets, and return the new key for operator deployment.
func serveKMSRotate(pool *db.Pool, w http.ResponseWriter, r *http.Request, key []byte) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")

	newHex, err := crypto.GenerateKey()
	if err != nil {
		jsonError(w, http.StatusInternalServerError, "generate key failed: "+err.Error())
		return
	}
	newKey, _ := hex.DecodeString(newHex)

	count, err := rotateKeyAndReencrypt(r.Context(), pool, key, newKey)
	if err != nil {
		jsonError(w, http.StatusBadGateway, "re-encrypt failed: "+err.Error())
		return
	}

	_ = json.NewEncoder(w).Encode(map[string]any{
		"ok":             true,
		"new_key_hex":    newHex,
		"secrets_rotated": count,
		"note":           "Set KMS_MASTER_KEY to the new_key_hex value and restart. Keep the old key until all replicas have rotated.",
	})
}

// serveDataMask handles POST /iam/mask — apply PII masking to input text.
// This is a utility endpoint for testing the masking rules.
func serveDataMask(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")

	var req struct {
		Text string `json:"text"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, http.StatusBadRequest, "invalid json body")
		return
	}
	masked := crypto.MaskPII(req.Text)
	_ = json.NewEncoder(w).Encode(map[string]any{"original": req.Text, "masked": masked})
}

// randHex returns n random bytes encoded as hex.
func randHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}
