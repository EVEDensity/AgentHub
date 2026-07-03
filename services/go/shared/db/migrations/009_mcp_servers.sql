-- Sprint P1-2: MCP Gateway Server Configuration
-- Stores MCP (Model Context Protocol) server connection configurations
-- so users can register external MCP-compatible servers and discover their tools.
-- Idempotent migration — uses IF NOT EXISTS.

-- ── MCP Server Configurations ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS platform_mcp_servers (
    id              TEXT PRIMARY KEY,                     -- uuid v7
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    name            TEXT NOT NULL,                         -- user-facing display name
    description     TEXT NOT NULL DEFAULT '',              -- short description
    transport       TEXT NOT NULL DEFAULT 'sse',           -- 'stdio' | 'sse'
    -- STDIO transport fields
    command         TEXT NOT NULL DEFAULT '',              -- e.g. 'node', 'python', 'uvx'
    args            TEXT[] NOT NULL DEFAULT '{}',          -- e.g. ['server.js']
    env_vars        JSONB NOT NULL DEFAULT '{}',           -- e.g. {"NODE_ENV":"production"}
    -- SSE transport fields
    url             TEXT NOT NULL DEFAULT '',              -- e.g. 'http://localhost:8099/mcp'
    -- Status
    status          TEXT NOT NULL DEFAULT 'unknown',       -- 'connected' | 'disconnected' | 'error' | 'unknown'
    last_connected_at TIMESTAMPTZ,
    error_message   TEXT NOT NULL DEFAULT '',
    -- Metadata
    tags            TEXT[] NOT NULL DEFAULT '{}',          -- e.g. ['knowledge', 'search']
    created_by      TEXT NOT NULL DEFAULT 'system',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, name)
);

-- ── Indexes ──────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_mcp_servers_tenant
    ON platform_mcp_servers (tenant_id);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_status
    ON platform_mcp_servers (tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_transport
    ON platform_mcp_servers (tenant_id, transport);

-- ── Trigger: auto-update updated_at ──────────────────────────────────

CREATE OR REPLACE FUNCTION update_mcp_server_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trigger_mcp_servers_updated_at'
    ) THEN
        CREATE TRIGGER trigger_mcp_servers_updated_at
            BEFORE UPDATE ON platform_mcp_servers
            FOR EACH ROW EXECUTE FUNCTION update_mcp_server_updated_at();
    END IF;
END;
$$;
