-- Sprint J: Digital Identity + Docker Sandbox + Workspace
-- Idempotent migration — all CREATE TABLE use IF NOT EXISTS.

-- ── J1: Agent Digital Identity ──────────────────────────────────────
-- Each agent can have a digital identity card: email, SSH key, GPG key,
-- OAuth2 credentials. This enables agents to act as first-class digital
-- entities that can send/receive email, authenticate via SSH, and call
-- external APIs with OAuth2 tokens.

CREATE TABLE IF NOT EXISTS platform_agent_identities (
    id               TEXT PRIMARY KEY,
    agent_id         TEXT NOT NULL UNIQUE,
    tenant_id        TEXT NOT NULL,
    email            TEXT NOT NULL DEFAULT '',
    ssh_pubkey       TEXT NOT NULL DEFAULT '',
    ssh_key_type     TEXT NOT NULL DEFAULT 'ed25519',
    gpg_key          TEXT NOT NULL DEFAULT '',
    oauth2_provider  TEXT NOT NULL DEFAULT '',
    oauth2_creds     TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_identities_agent ON platform_agent_identities(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_identities_tenant ON platform_agent_identities(tenant_id);
CREATE INDEX IF NOT EXISTS idx_agent_identities_status ON platform_agent_identities(status);

-- ── J2: Docker Sandbox Containers ───────────────────────────────────
-- Tracks sandbox container lifecycle: specs, status, resource usage.

CREATE TABLE IF NOT EXISTS platform_sandbox_containers (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    container_name  TEXT NOT NULL DEFAULT '',
    image           TEXT NOT NULL DEFAULT 'agenthub/sandbox:latest',
    status          TEXT NOT NULL DEFAULT 'created',
    cpu_limit       REAL NOT NULL DEFAULT 1.0,
    memory_mb       INT NOT NULL DEFAULT 512,
    disk_mb         INT NOT NULL DEFAULT 10240,
    network_allow   TEXT[] DEFAULT '{}',
    workspace_path  TEXT NOT NULL DEFAULT '',
    seccomp_profile TEXT NOT NULL DEFAULT 'default',
    started_at      TIMESTAMPTZ,
    stopped_at      TIMESTAMPTZ,
    idle_timeout_s  INT NOT NULL DEFAULT 1800,
    max_runtime_s   INT NOT NULL DEFAULT 7200,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sandbox_agent ON platform_sandbox_containers(agent_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_tenant ON platform_sandbox_containers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_status ON platform_sandbox_containers(status);

-- ── J2: Sandbox Execution Logs ──────────────────────────────────────
-- Records every command executed inside a sandbox container.

CREATE TABLE IF NOT EXISTS platform_sandbox_exec_logs (
    id            TEXT PRIMARY KEY,
    container_id  TEXT NOT NULL REFERENCES platform_sandbox_containers(id) ON DELETE CASCADE,
    agent_id      TEXT NOT NULL,
    tenant_id     TEXT NOT NULL,
    command       TEXT NOT NULL DEFAULT '',
    exit_code     INT NOT NULL DEFAULT 0,
    stdout        TEXT NOT NULL DEFAULT '',
    stderr        TEXT NOT NULL DEFAULT '',
    duration_ms   INT NOT NULL DEFAULT 0,
    executed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_exec_logs_container ON platform_sandbox_exec_logs(container_id);
CREATE INDEX IF NOT EXISTS idx_exec_logs_agent ON platform_sandbox_exec_logs(agent_id);
