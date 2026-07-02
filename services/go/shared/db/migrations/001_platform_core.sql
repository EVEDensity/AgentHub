-- 001_platform_core.sql
-- Platform core schema for the Go online tier. Uses platform_ prefix to avoid
-- collisions with the legacy Python monolith tables during dual-track migration.
-- All tables carry tenant_id for multi-tenant isolation. Idempotent (IF NOT EXISTS).

-- ── Schema migrations tracking ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_schema_migrations (
    version  TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Sessions (source of truth, Redis holds hot state) ──────────────────
CREATE TABLE IF NOT EXISTS platform_sessions (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    owner_id     TEXT NOT NULL DEFAULT '',
    type         TEXT NOT NULL DEFAULT 'group',
    visibility   TEXT NOT NULL DEFAULT 'private',
    status       TEXT NOT NULL DEFAULT 'active',
    last_message_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_sessions_tenant ON platform_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_platform_sessions_owner  ON platform_sessions(owner_id);

CREATE TABLE IF NOT EXISTS platform_session_members (
    session_id  TEXT NOT NULL REFERENCES platform_sessions(id) ON DELETE CASCADE,
    tenant_id   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'member',
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_platform_session_members_user ON platform_session_members(user_id);

-- ── Messages (partitioned by month for scale) ──────────────────────────
-- Parent table; child partitions created by a maintenance job. For P0 we use
-- a plain table with tenant_id indexing; partitioning is a P3 hardening step.
CREATE TABLE IF NOT EXISTS platform_messages (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    sender        TEXT NOT NULL DEFAULT '',
    actor_id      TEXT NOT NULL DEFAULT '',
    content       TEXT NOT NULL DEFAULT '',
    type          TEXT NOT NULL DEFAULT 'text',
    trace_id      TEXT NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_messages_session ON platform_messages(tenant_id, session_id, created_at);

-- ── Permission requests & decisions (audit trail) ──────────────────────
CREATE TABLE IF NOT EXISTS platform_permission_requests (
    id              TEXT PRIMARY KEY,            -- request_id
    tenant_id       TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    trace_id        TEXT NOT NULL DEFAULT '',
    actor_id        TEXT NOT NULL DEFAULT '',
    tool_name       TEXT NOT NULL,
    risk_level      TEXT NOT NULL DEFAULT 'normal',
    reason          TEXT NOT NULL DEFAULT '',
    arguments_json  TEXT NOT NULL DEFAULT '{}',
    decision        TEXT NOT NULL DEFAULT 'pending',
    decided_by      TEXT NOT NULL DEFAULT '',
    timeout_seconds INTEGER NOT NULL DEFAULT 30,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_platform_perm_req_tenant ON platform_permission_requests(tenant_id, decision);
CREATE INDEX IF NOT EXISTS idx_platform_perm_req_session ON platform_permission_requests(session_id);

-- ── Audit events (partitioned by week in prod; plain table for P0) ─────
CREATE TABLE IF NOT EXISTS platform_audit_events (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    session_id    TEXT NOT NULL DEFAULT '',
    trace_id      TEXT NOT NULL DEFAULT '',
    event_type    TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT '',
    actor_id      TEXT NOT NULL DEFAULT '',
    payload_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_audit_tenant ON platform_audit_events(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_platform_audit_session ON platform_audit_events(session_id);

-- ── Envelope archive (debug/replay for event-driven flows) ─────────────
CREATE TABLE IF NOT EXISTS platform_envelopes (
    event_id      TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    session_id    TEXT NOT NULL DEFAULT '',
    trace_id      TEXT NOT NULL DEFAULT '',
    message_id    TEXT NOT NULL DEFAULT '',
    event_type    TEXT NOT NULL,
    producer_svc  TEXT NOT NULL DEFAULT '',
    producer_inst TEXT NOT NULL DEFAULT '',
    payload_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_envelopes_session ON platform_envelopes(tenant_id, session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_platform_envelopes_trace   ON platform_envelopes(trace_id);
