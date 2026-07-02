-- 004_kms_secrets.sql
-- P3-4: Secret Manager / KMS storage. API keys and other secrets are
-- encrypted at rest with AES-256-GCM (keyed by KMS_MASTER_KEY env var).
-- Only metadata (name/tenant/provider/rotation dates) is queryable in
-- plaintext; the secret body requires crypto.Decrypt.
-- Idempotent (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS platform_secrets (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,                  -- human-readable label
    tenant_id        TEXT NOT NULL DEFAULT '',       -- '' = system-wide
    provider         TEXT NOT NULL DEFAULT '',       -- openai | anthropic | bge | custom
    encrypted_secret TEXT NOT NULL DEFAULT '',       -- AES-256-GCM base64
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_secrets_tenant ON platform_secrets(tenant_id, provider);
