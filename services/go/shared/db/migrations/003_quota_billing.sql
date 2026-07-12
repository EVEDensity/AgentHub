-- 003_quota_billing.sql
-- P3-2: Multi-tenant quota enforcement, usage tracking, and billing cycles.
-- Each plan (free/pro/enterprise) has default quotas stored in
-- platform_quota_definitions; per-tenant overrides live in the quotas_json
-- column on platform_tenants (created in 002). Usage is tracked in a
-- rolling window keyed by billing cycle.
-- Idempotent (IF NOT EXISTS).

-- ── Plan-based quota defaults ──────────────────────────────────────────
-- The source of truth for plan limits. tenant-level overrides in
-- platform_tenants.quotas_json take precedence at enforcement time.
CREATE TABLE IF NOT EXISTS platform_quota_definitions (
    plan              TEXT PRIMARY KEY,              -- free | pro | enterprise
    daily_tokens      BIGINT NOT NULL DEFAULT 0,    -- max tokens per day (0 = unlimited)
    monthly_tokens    BIGINT NOT NULL DEFAULT 0,    -- max tokens per month
    max_sessions      INT NOT NULL DEFAULT 10,      -- max active sessions
    max_agents        INT NOT NULL DEFAULT 3,       -- max registered agents
    max_concurrent    INT NOT NULL DEFAULT 2        -- max concurrent agent runs
);
CREATE INDEX IF NOT EXISTS idx_platform_quota_defs_plan ON platform_quota_definitions(plan);

-- Seed default plans.
INSERT INTO platform_quota_definitions (plan, daily_tokens, monthly_tokens, max_sessions, max_agents, max_concurrent) VALUES
    ('free',        100000,     3000000,    10,  3,  2),
    ('pro',        1000000,    30000000,    50, 20, 10),
    ('enterprise',        0,          0,   200, 100, 50)
ON CONFLICT (plan) DO NOTHING;

-- ── Usage tracking ─────────────────────────────────────────────────────
-- Each row is an atomic usage event: token consumption, session creation,
-- agent dispatch, etc. Aggregation is done in queries so we don't need a
-- separate materialised view (the volume is low enough per tenant that
-- on-the-fly SUM is fine even at ~1M rows per day).
CREATE TABLE IF NOT EXISTS platform_usage_events (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    session_id    TEXT NOT NULL DEFAULT '',
    event_type    TEXT NOT NULL,                    -- token_consumed | session_created | agent_dispatched
    amount        BIGINT NOT NULL DEFAULT 1,       -- consumed units (1 for session, N for tokens)
    meta_json     TEXT NOT NULL DEFAULT '{}',      -- model, pool, agent_role, etc.
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_usage_tenant ON platform_usage_events(tenant_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_platform_usage_session ON platform_usage_events(session_id);

-- ── Billing cycles ─────────────────────────────────────────────────────
-- Each tenant has one row per billing period (monthly). cycle_start/end
-- define the window; usage within that window is summed from platform_usage_events.
CREATE TABLE IF NOT EXISTS platform_billing_cycles (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    cycle_start     TIMESTAMPTZ NOT NULL,
    cycle_end       TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',   -- open | closed | archived
    total_tokens    BIGINT NOT NULL DEFAULT 0,
    total_sessions  INT NOT NULL DEFAULT 0,
    total_agents    INT NOT NULL DEFAULT 0,
    invoice_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_platform_billing_cycles_tenant ON platform_billing_cycles(tenant_id, cycle_start);
