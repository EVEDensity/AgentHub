-- 014_workspace_acl_enhancement.sql
-- P0.3: RBAC enhancement — add agent_operator role, workspace/model scopes,
-- and workspace ACL permissions column. Idempotent (IF NOT EXISTS / ON CONFLICT).
--
-- Changes:
--   1. Seed agent_operator system role (between member and tenant_admin)
--   2. Add workspace:admin, workspace:read, model:manage scopes to relevant roles
--   3. Add permissions JSONB column to platform_workspace_members for fine-grained ACL
--   4. Add workspace:read scope to member and viewer (baseline workspace access)

-- ── 1. Seed agent_operator system role ─────────────────────────────────
INSERT INTO platform_roles (id, tenant_id, name, description, is_system) VALUES
    ('role-agent-operator', '', 'agent_operator',
     'Agent operator: can execute high-risk tools, manage workspaces and models, but cannot manage tenant members/roles/billing.',
     true)
ON CONFLICT (id) DO NOTHING;

-- ── 2. agent_operator scopes ───────────────────────────────────────────
-- Mirrors iam.DefaultRoleScopes[RoleAgentOperator] in rbac.go
INSERT INTO platform_role_permissions (role_id, tenant_id, scope) VALUES
    ('role-agent-operator', '', 'session:read'),
    ('role-agent-operator', '', 'session:write'),
    ('role-agent-operator', '', 'session:create'),
    ('role-agent-operator', '', 'agent:dispatch'),
    ('role-agent-operator', '', 'agent:read'),
    ('role-agent-operator', '', 'tool:execute'),
    ('role-agent-operator', '', 'tool:approve'),
    ('role-agent-operator', '', 'memory:read'),
    ('role-agent-operator', '', 'memory:write'),
    ('role-agent-operator', '', 'document:upload'),
    ('role-agent-operator', '', 'document:read'),
    ('role-agent-operator', '', 'workspace:admin'),
    ('role-agent-operator', '', 'workspace:read'),
    ('role-agent-operator', '', 'model:manage')
ON CONFLICT (role_id, scope) DO NOTHING;

-- ── 3. Add new scopes to tenant_admin ──────────────────────────────────
INSERT INTO platform_role_permissions (role_id, tenant_id, scope) VALUES
    ('role-tenant-admin', '', 'workspace:admin'),
    ('role-tenant-admin', '', 'workspace:read'),
    ('role-tenant-admin', '', 'model:manage')
ON CONFLICT (role_id, scope) DO NOTHING;

-- ── 4. Add workspace:read to member and viewer ─────────────────────────
INSERT INTO platform_role_permissions (role_id, tenant_id, scope) VALUES
    ('role-member', '', 'workspace:read'),
    ('role-viewer', '', 'workspace:read')
ON CONFLICT (role_id, scope) DO NOTHING;

-- ── 5. Add permissions column to workspace_members ─────────────────────
-- Allows fine-grained scope overrides per workspace (e.g. grant tool:approve
-- only in a specific workspace). NULL means use the workspace role's default
-- scopes. The Go WorkspacePolicy type loads this into memory.
ALTER TABLE platform_workspace_members
    ADD COLUMN IF NOT EXISTS permissions JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN platform_workspace_members.permissions IS
    'P0.3: Additional scope strings granted to this user in this workspace (JSON array). NULL or empty = use role defaults.';

-- ── 6. Document the 5-role RBAC matrix ────────────────────────────────
COMMENT ON TABLE platform_roles IS
    'P0.3: System roles: super_admin, tenant_admin, agent_operator, member, viewer. agent_operator (P0.3) can execute high-risk tools and manage workspaces/models but cannot manage tenant members/roles/billing.';
