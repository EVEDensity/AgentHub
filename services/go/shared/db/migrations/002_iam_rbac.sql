-- 002_iam_rbac.sql
-- Multi-tenant identity & access management schema (P3-1). Adds tenant
-- registry, user registry, RBAC roles/permissions, user-role assignments, and
-- sensitive-tool classification. All tables carry tenant_id for isolation.
-- Idempotent (IF NOT EXISTS). The 001 migration created platform_sessions etc.

-- ── Tenants ────────────────────────────────────────────────────────────
-- The tenant registry. plan drives default quotas (enforced by P3-2); status
-- gates active access (active/suspended/closed). quotas_json stores the merged
-- quota overrides as JSON so the quota service can read them without joins.
CREATE TABLE IF NOT EXISTS platform_tenants (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    plan         TEXT NOT NULL DEFAULT 'free',     -- free | pro | enterprise
    status       TEXT NOT NULL DEFAULT 'active',   -- active | suspended | closed
    quotas_json  TEXT NOT NULL DEFAULT '{}',       -- P3-2 quota overrides
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Users ──────────────────────────────────────────────────────────────
-- A user belongs to exactly one tenant (single-tenant membership for v1; a
-- mapping table can add cross-tenant membership later). email is unique per
-- tenant. status gates login (active/disabled).
CREATE TABLE IF NOT EXISTS platform_users (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES platform_tenants(id) ON DELETE CASCADE,
    email        TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',   -- active | disabled
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)
);
CREATE INDEX IF NOT EXISTS idx_platform_users_tenant ON platform_users(tenant_id);

-- ── Roles ──────────────────────────────────────────────────────────────
-- Role definitions. System roles (super_admin/tenant_admin/member/viewer) are
-- seeded with is_system=true and shared across tenants via tenant_id=''. Tenant
-- admins can define custom roles scoped to their tenant_id.
CREATE TABLE IF NOT EXISTS platform_roles (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL DEFAULT '',         -- '' = system-wide role
    name         TEXT NOT NULL,                    -- role name (e.g. "oncall")
    description  TEXT NOT NULL DEFAULT '',
    is_system    BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

-- ── Role permissions ───────────────────────────────────────────────────
-- Scopes granted by a role. System roles are seeded from iam.DefaultRoleScopes;
-- tenant custom roles list their scopes here. tenant_id is denormalized for
-- fast filtered queries without a join.
CREATE TABLE IF NOT EXISTS platform_role_permissions (
    role_id      TEXT NOT NULL REFERENCES platform_roles(id) ON DELETE CASCADE,
    tenant_id    TEXT NOT NULL DEFAULT '',
    scope        TEXT NOT NULL,                    -- e.g. "session:write"
    PRIMARY KEY (role_id, scope)
);
CREATE INDEX IF NOT EXISTS idx_platform_role_perm_tenant ON platform_role_permissions(tenant_id);

-- ── User-role assignments ──────────────────────────────────────────────
-- Maps users to roles within a tenant. A user may hold multiple roles; the
-- effective scope set is the union. tenant_id is denormalized.
CREATE TABLE IF NOT EXISTS platform_user_roles (
    tenant_id    TEXT NOT NULL,
    user_id      TEXT NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
    role_id      TEXT NOT NULL REFERENCES platform_roles(id) ON DELETE CASCADE,
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role_id)
);
CREATE INDEX IF NOT EXISTS idx_platform_user_roles_tenant ON platform_user_roles(tenant_id);

-- ── Sensitive tool classification ──────────────────────────────────────
-- Per-tenant risk classification for tools. When a tool is invoked, the
-- tool-permission-service looks up this table (then falls back to the builtin
-- pattern table in iam.BuiltinToolRisk). requires_confirmation drives the
-- two-step approval flow; allowed_roles restricts who may confirm critical
-- tools (stored as a comma-separated list for portability).
CREATE TABLE IF NOT EXISTS platform_sensitive_tools (
    tenant_id             TEXT NOT NULL,
    tool_name             TEXT NOT NULL,           -- exact match (case-insensitive at query)
    risk_level            TEXT NOT NULL DEFAULT 'normal', -- low | normal | high | critical
    requires_confirmation BOOLEAN NOT NULL DEFAULT false,
    allowed_roles         TEXT NOT NULL DEFAULT '',       -- comma-separated role names
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, tool_name)
);

-- ── Seed system roles ──────────────────────────────────────────────────
-- Idempotent: ON CONFLICT skips rows that already exist. These mirror
-- iam.DefaultRoleScopes so the DB is the source of truth for token issuance.
INSERT INTO platform_roles (id, tenant_id, name, description, is_system) VALUES
    ('role-super-admin', '', 'super_admin',  'Cross-tenant break-glass; bypasses all checks.', true),
    ('role-tenant-admin','', 'tenant_admin', 'Tenant-scoped administrator.', true),
    ('role-member',      '', 'member',       'Standard user.', true),
    ('role-viewer',      '', 'viewer',       'Read-only observer.', true)
ON CONFLICT (id) DO NOTHING;

INSERT INTO platform_role_permissions (role_id, tenant_id, scope) VALUES
    ('role-super-admin', '', '*'),
    ('role-tenant-admin','', 'session:read'),
    ('role-tenant-admin','', 'session:write'),
    ('role-tenant-admin','', 'session:create'),
    ('role-tenant-admin','', 'session:delete'),
    ('role-tenant-admin','', 'agent:dispatch'),
    ('role-tenant-admin','', 'agent:read'),
    ('role-tenant-admin','', 'tool:execute'),
    ('role-tenant-admin','', 'tool:approve'),
    ('role-tenant-admin','', 'memory:read'),
    ('role-tenant-admin','', 'memory:write'),
    ('role-tenant-admin','', 'document:upload'),
    ('role-tenant-admin','', 'document:read'),
    ('role-tenant-admin','', 'audit:read'),
    ('role-tenant-admin','', 'tenant:manage'),
    ('role-tenant-admin','', 'role:manage'),
    ('role-tenant-admin','', 'billing:read'),
    ('role-member',      '', 'session:read'),
    ('role-member',      '', 'session:write'),
    ('role-member',      '', 'session:create'),
    ('role-member',      '', 'agent:dispatch'),
    ('role-member',      '', 'agent:read'),
    ('role-member',      '', 'tool:execute'),
    ('role-member',      '', 'memory:read'),
    ('role-member',      '', 'memory:write'),
    ('role-member',      '', 'document:upload'),
    ('role-member',      '', 'document:read'),
    ('role-viewer',      '', 'session:read'),
    ('role-viewer',      '', 'agent:read'),
    ('role-viewer',      '', 'document:read'),
    ('role-viewer',      '', 'audit:read')
ON CONFLICT (role_id, scope) DO NOTHING;
