-- 010_a2a_agent_cards.sql
-- A2A (Agent-to-Agent) Protocol — external agent registry.
-- Stores discovered and manually registered A2A agent cards for
-- cross-agent interoperability.

CREATE TABLE IF NOT EXISTS platform_a2a_agents (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    name            TEXT NOT NULL,
    description     TEXT,
    url             TEXT NOT NULL,
    protocol_version TEXT DEFAULT '1.0',
    provider_name   TEXT,
    provider_url    TEXT,
    provider_org    TEXT,
    capabilities    JSONB DEFAULT '{}',
    skills          JSONB DEFAULT '[]',
    endpoints       JSONB DEFAULT '{}',
    auth_schemes    JSONB DEFAULT '[]',
    version         TEXT,
    documentation   TEXT,
    icon_url        TEXT,
    source          TEXT DEFAULT 'external',  -- 'internal' | 'external'
    status          TEXT DEFAULT 'active',     -- 'active' | 'inactive' | 'error'
    error_message   TEXT,
    last_seen_at    TIMESTAMPTZ,
    tags            TEXT[],
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_a2a_agents_tenant ON platform_a2a_agents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_a2a_agents_status ON platform_a2a_agents(status);
CREATE INDEX IF NOT EXISTS idx_a2a_agents_source ON platform_a2a_agents(source);
CREATE UNIQUE INDEX IF NOT EXISTS idx_a2a_agents_url ON platform_a2a_agents(tenant_id, url);
CREATE INDEX IF NOT EXISTS idx_a2a_agents_skills ON platform_a2a_agents USING gin(skills);
CREATE INDEX IF NOT EXISTS idx_a2a_agents_tags ON platform_a2a_agents USING gin(tags);

-- Auto-update trigger
DO $$ BEGIN
    CREATE TRIGGER a2a_agents_updated_at
        BEFORE UPDATE ON platform_a2a_agents
        FOR EACH ROW EXECUTE PROCEDURE update_updated_at();
EXCEPTION WHEN undefined_function THEN
    -- Trigger function not yet created; skip.
END $$;
