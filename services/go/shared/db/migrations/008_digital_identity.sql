-- 008_digital_identity.sql
-- Sprint J: Digital Identity & Sandbox persistence (P3-1/P3-3).
-- Stores agent digital identities (SSH keys, OAuth2 creds, GPG keys) and
-- sandbox container lifecycle records with execution audit logs.
-- Idempotent (IF NOT EXISTS).

-- ── Agent Digital Identities ───────────────────────────────────────────
-- One identity per agent_id. SSH keys, OAuth2 credentials, and GPG keys
-- enable agents to authenticate with external systems (git, APIs, CI/CD).
-- Status lifecycle: pending → active → suspended → revoked.
CREATE TABLE IF NOT EXISTS agentnet_identities (
    id               TEXT PRIMARY KEY,
    agent_id         TEXT NOT NULL UNIQUE,
    tenant_id        TEXT NOT NULL DEFAULT '',
    email            TEXT NOT NULL DEFAULT '',
    ssh_pubkey       TEXT NOT NULL DEFAULT '',
    ssh_key_type     TEXT NOT NULL DEFAULT 'ed25519',
    gpg_key          TEXT NOT NULL DEFAULT '',
    oauth2_provider  TEXT NOT NULL DEFAULT '',
    oauth2_creds     TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'pending',  -- pending | active | suspended | revoked
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentnet_identities_agent ON agentnet_identities(agent_id);
CREATE INDEX IF NOT EXISTS idx_agentnet_identities_tenant ON agentnet_identities(tenant_id);
CREATE INDEX IF NOT EXISTS idx_agentnet_identities_status ON agentnet_identities(status);

-- ── Sandbox Containers ─────────────────────────────────────────────────
-- Docker sandbox lifecycle records. Each container is tied to an agent.
-- Security constraints (CPU/memory/disk limits, seccomp profile, network
-- allowlist) are persisted for audit and re-creation.
CREATE TABLE IF NOT EXISTS sandbox_containers (
    id               TEXT PRIMARY KEY,
    agent_id         TEXT NOT NULL,
    tenant_id        TEXT NOT NULL DEFAULT '',
    container_name   TEXT NOT NULL DEFAULT '',
    image            TEXT NOT NULL DEFAULT 'agenthub/sandbox:latest',
    status           TEXT NOT NULL DEFAULT 'created',  -- created|starting|running|stopped|failed|destroyed
    cpu_limit        REAL NOT NULL DEFAULT 1.0,
    memory_mb        INTEGER NOT NULL DEFAULT 512,
    disk_mb          INTEGER NOT NULL DEFAULT 10240,
    network_allow    TEXT[] NOT NULL DEFAULT '{}',
    workspace_path   TEXT NOT NULL DEFAULT '',
    seccomp_profile  TEXT NOT NULL DEFAULT 'default',
    idle_timeout_s   INTEGER NOT NULL DEFAULT 1800,
    max_runtime_s    INTEGER NOT NULL DEFAULT 7200,
    started_at       TIMESTAMPTZ,
    stopped_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sandbox_containers_agent ON sandbox_containers(agent_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_containers_tenant ON sandbox_containers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_containers_status ON sandbox_containers(status);

-- ── Sandbox Execution Logs ─────────────────────────────────────────────
-- Immutable audit trail of every command executed inside a sandbox.
-- Retained for 90 days (partitioned by month in production).
CREATE TABLE IF NOT EXISTS sandbox_exec_logs (
    id               TEXT PRIMARY KEY,
    container_id     TEXT NOT NULL,
    agent_id         TEXT NOT NULL,
    tenant_id        TEXT NOT NULL DEFAULT '',
    command          TEXT NOT NULL DEFAULT '',
    exit_code        INTEGER NOT NULL DEFAULT 0,
    stdout           TEXT NOT NULL DEFAULT '',
    stderr           TEXT NOT NULL DEFAULT '',
    duration_ms      INTEGER NOT NULL DEFAULT 0,
    executed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sandbox_exec_logs_container ON sandbox_exec_logs(container_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_exec_logs_agent ON sandbox_exec_logs(agent_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_exec_logs_ts ON sandbox_exec_logs(executed_at);
