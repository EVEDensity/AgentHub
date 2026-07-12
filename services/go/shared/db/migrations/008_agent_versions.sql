-- Sprint P1-6: Agent Version Management
-- Every time an agent config is saved, a snapshot is written here.
-- Supports version timeline, diff comparison, and one-click rollback.
-- Idempotent migration — uses IF NOT EXISTS.

-- ── Agent Versions Table ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS platform_agent_versions (
    id              TEXT PRIMARY KEY,                    -- uuid v7
    agent_id        TEXT NOT NULL,                       -- FK to platform_agents.agent_id
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    version         INT NOT NULL,                        -- monotonically increasing per agent
    snapshot        JSONB NOT NULL,                      -- full agent config at this version
    change_summary  TEXT NOT NULL DEFAULT '',             -- human-readable summary
    changed_fields  TEXT[] NOT NULL DEFAULT '{}',         -- list of field keys that changed
    created_by      TEXT NOT NULL DEFAULT 'system',       -- user who triggered the save
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (agent_id, version)
);

CREATE INDEX IF NOT EXISTS idx_agent_versions_agent
    ON platform_agent_versions (agent_id);

CREATE INDEX IF NOT EXISTS idx_agent_versions_agent_version
    ON platform_agent_versions (agent_id, version DESC);

CREATE INDEX IF NOT EXISTS idx_agent_versions_created_at
    ON platform_agent_versions (agent_id, created_at DESC);

-- ── Helper: next_version function ────────────────────────────────

CREATE OR REPLACE FUNCTION next_agent_version(_agent_id TEXT)
RETURNS INT AS $$
DECLARE
    _next INT;
BEGIN
    SELECT COALESCE(MAX(version), 0) + 1
    INTO _next
    FROM platform_agent_versions
    WHERE agent_id = _agent_id;
    RETURN _next;
END;
$$ LANGUAGE plpgsql STABLE;
