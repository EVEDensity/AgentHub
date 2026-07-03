-- 012: A/B Testing Framework
-- Sprint L5 — experiment management, impression logging, and result storage.

-- ── A/B Experiments ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_ab_experiments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    agent_id        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','running','paused','completed')),
    traffic_split   INTEGER NOT NULL DEFAULT 50
                    CHECK (traffic_split BETWEEN 1 AND 99),
    variants        JSONB NOT NULL DEFAULT '[]',
    metrics_config  JSONB NOT NULL DEFAULT '{"quality":0.4,"latency":0.2,"token_usage":0.15,"success_rate":0.15,"user_satisfaction":0.1}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ
);

-- ── A/B Impressions ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_ab_impressions (
    id              BIGSERIAL PRIMARY KEY,
    experiment_id   UUID NOT NULL REFERENCES platform_ab_experiments(id) ON DELETE CASCADE,
    variant_id      TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    metrics         JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ab_impressions_exp_var
    ON platform_ab_impressions(experiment_id, variant_id);
CREATE INDEX IF NOT EXISTS idx_ab_impressions_exp_time
    ON platform_ab_impressions(experiment_id, created_at);

-- ── A/B Results (cached computation) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_ab_results (
    experiment_id       UUID PRIMARY KEY REFERENCES platform_ab_experiments(id) ON DELETE CASCADE,
    winner_variant_id   TEXT,
    confidence_level    DOUBLE PRECISION,
    p_value             DOUBLE PRECISION,
    effect_size         DOUBLE PRECISION,
    variant_stats       JSONB,
    test_method         TEXT NOT NULL DEFAULT 'ttest',
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
