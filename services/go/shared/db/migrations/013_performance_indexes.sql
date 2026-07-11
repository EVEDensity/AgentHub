-- ============================================================================
-- Sprint M1: Database Query Optimization
-- Performance indexes for high-traffic query patterns identified during
-- load testing and slow-query analysis.
-- ============================================================================

BEGIN;

-- ── 1. Composite indexes for platform_context_segments ─────────────────
-- High-frequency query pattern: SELECT ... WHERE tenant_id AND session_id ORDER BY created_at
-- Used by context_engine.go for context recall and memory retrieval.

CREATE INDEX IF NOT EXISTS idx_context_tenant_session_created
    ON platform_context_segments (tenant_id, session_id, created_at DESC);

-- Partial index for active (non-expired) context segments
CREATE INDEX IF NOT EXISTS idx_context_active_segments
    ON platform_context_segments (tenant_id, created_at DESC)
    WHERE expires_at IS NULL OR expires_at > now();

-- Index on entity_id for multi-layer memory retrieval
CREATE INDEX IF NOT EXISTS idx_context_entity
    ON platform_context_segments (entity_id)
    WHERE entity_id IS NOT NULL AND entity_id != '';

-- ── 2. Indexes for platform_audit_events ──────────────────────────────
-- Frequently queried by time range with tenant filter

CREATE INDEX IF NOT EXISTS idx_audit_tenant_created
    ON platform_audit_events (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_event_type_created
    ON platform_audit_events (event_type, created_at DESC);

-- Partial index for recent audit events (most common query pattern)
CREATE INDEX IF NOT EXISTS idx_audit_recent_events
    ON platform_audit_events (created_at DESC)
    WHERE created_at > now() - interval '7 days';

-- ── 3. Indexes for platform_sessions (high-cardinality table) ─────────
-- Optimize session lookup by tenant + session_id (most common access pattern)

CREATE INDEX IF NOT EXISTS idx_sessions_tenant_session
    ON platform_sessions (tenant_id, session_id);

-- Index for active sessions cleanup
CREATE INDEX IF NOT EXISTS idx_sessions_last_active
    ON platform_sessions (last_active_at DESC)
    WHERE status = 'active';

-- ── 4. Indexes for platform_ab_impressions (high-volume time-series) ──
-- Most queries filter by experiment_id + time range

CREATE INDEX IF NOT EXISTS idx_ab_impressions_experiment_time
    ON platform_ab_impressions (experiment_id, created_at DESC);

-- Partial index for recent impressions (most statistical queries use recent data)
CREATE INDEX IF NOT EXISTS idx_ab_impressions_recent
    ON platform_ab_impressions (experiment_id, variant_id, created_at DESC)
    WHERE created_at > now() - interval '30 days';

-- ── 5. Indexes for platform_agent_registry ─────────────────────────────
-- Agent list by workspace + tenant is a common admin page query

CREATE INDEX IF NOT EXISTS idx_agents_workspace_tenant
    ON platform_agent_registry (workspace_id, tenant_id)
    WHERE workspace_id IS NOT NULL AND workspace_id != '';

-- ── 6. Indexes for platform_golden_items ───────────────────────────────
-- Eval runner loads all items for a dataset in index order

CREATE INDEX IF NOT EXISTS idx_golden_items_dataset
    ON platform_golden_items (dataset_id, index);

-- ── 7. Indexes for platform_workspace_members ──────────────────────────
-- Member lookup during permission checks

CREATE INDEX IF NOT EXISTS idx_workspace_members_user
    ON platform_workspace_members (user_id, workspace_id);

-- ── 8. BRIN indexes for append-only large tables ──────────────────────
-- Use BRIN (Block Range INdex) for time-series append-only tables
-- Much smaller than B-tree — ideal for very large tables.

CREATE INDEX IF NOT EXISTS idx_ab_impressions_brin_time
    ON platform_ab_impressions USING BRIN (created_at)
    WITH (pages_per_range = 32);

CREATE INDEX IF NOT EXISTS idx_audit_events_brin_time
    ON platform_audit_events USING BRIN (created_at)
    WITH (pages_per_range = 32);

-- ── 9. Enable pg_stat_statements if available (requires superuser) ────
-- This is a no-op if not installed; for slow query analysis in production.
-- Run manually: CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- ── 10. Connection pool tuning recommendations (env vars, not SQL) ────
-- The Go db.Pool uses these defaults (configurable via env vars):
--   DB_POOL_MAX_CONNS  = 20  →  increase to 50 for production
--   DB_POOL_MIN_CONNS  = 2   →  increase to 5  for production
--   DB_POOL_MAX_LIFETIME = 30m → reasonable for most workloads
--   DB_POOL_MAX_IDLE    = 5m  → reasonable for most workloads
--   DB_CONNECT_TIMEOUT  = 5s  → reasonable
-- Set via: export DB_POOL_MAX_CONNS=50 DB_POOL_MIN_CONNS=5

COMMIT;
