-- 012: Golden Datasets + Evaluation Runs
-- Sprint L6 — offline evaluation and regression testing infrastructure.
-- Idempotent migration — uses IF NOT EXISTS.

-- ── Golden Datasets ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_golden_datasets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    version     INTEGER NOT NULL DEFAULT 1,
    item_count  INTEGER NOT NULL DEFAULT 0,
    tags        TEXT[] NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_golden_datasets_tenant ON platform_golden_datasets(tenant_id);
CREATE INDEX IF NOT EXISTS idx_golden_datasets_tags ON platform_golden_datasets USING gin(tags);

-- ── Golden Items ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_golden_items (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id         UUID NOT NULL REFERENCES platform_golden_datasets(id) ON DELETE CASCADE,
    query              TEXT NOT NULL,
    expected_response  TEXT NOT NULL DEFAULT '',
    expected_chunk_ids TEXT[] NOT NULL DEFAULT '{}',
    expected_tool_calls JSONB NOT NULL DEFAULT '[]',
    metadata           JSONB NOT NULL DEFAULT '{}',
    index              INTEGER NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_golden_items_dataset ON platform_golden_items(dataset_id);
CREATE INDEX IF NOT EXISTS idx_golden_items_dataset_index ON platform_golden_items(dataset_id, index);

-- ── Evaluation Runs ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_eval_runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id   UUID REFERENCES platform_golden_datasets(id) ON DELETE SET NULL,
    tenant_id    TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','running','completed','failed','cancelled')),
    config       JSONB NOT NULL DEFAULT '{}',
    results      JSONB NOT NULL DEFAULT '{}',
    item_results JSONB NOT NULL DEFAULT '[]',
    started_at   TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_dataset ON platform_eval_runs(dataset_id);
CREATE INDEX IF NOT EXISTS idx_eval_runs_status ON platform_eval_runs(status);
CREATE INDEX IF NOT EXISTS idx_eval_runs_tenant ON platform_eval_runs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_eval_runs_created ON platform_eval_runs(created_at DESC);

-- ── Trigger: auto-update datasets updated_at ─────────────────────────
CREATE OR REPLACE FUNCTION update_golden_dataset_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trigger_golden_datasets_updated_at'
    ) THEN
        CREATE TRIGGER trigger_golden_datasets_updated_at
            BEFORE UPDATE ON platform_golden_datasets
            FOR EACH ROW EXECUTE FUNCTION update_golden_dataset_updated_at();
    END IF;
END;
$$;
