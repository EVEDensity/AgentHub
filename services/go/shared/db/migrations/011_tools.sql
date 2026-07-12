-- 011: Tool Marketplace + Agent-Tool Bindings
-- Sprint G1 — persistent tool registry and agent bindings.

-- ── Platform Tools ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_tools (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL DEFAULT '',
    name                        TEXT NOT NULL,
    description                 TEXT NOT NULL DEFAULT '',
    category                    TEXT NOT NULL DEFAULT 'general',
    icon                        TEXT NOT NULL DEFAULT 'build',
    parameters_json             JSONB NOT NULL DEFAULT '[]',
    return_type                 TEXT NOT NULL DEFAULT 'object',
    examples_json               JSONB NOT NULL DEFAULT '[]',
    risk_level                  TEXT NOT NULL DEFAULT 'L1',
    handler_type                TEXT NOT NULL DEFAULT 'builtin',
    enabled                     BOOLEAN NOT NULL DEFAULT true,
    is_concurrency_safe         BOOLEAN NOT NULL DEFAULT true,
    requires_user_confirmation  BOOLEAN NOT NULL DEFAULT false,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_tools_tenant ON platform_tools(tenant_id);
CREATE INDEX IF NOT EXISTS idx_platform_tools_category ON platform_tools(category);
CREATE INDEX IF NOT EXISTS idx_platform_tools_handler ON platform_tools(handler_type);

-- ── Agent-Tool Bindings ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_agent_tools (
    agent_id    TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT '',
    tool_ids    TEXT[] NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_plat_agent_tools_tenant ON platform_agent_tools(tenant_id);
