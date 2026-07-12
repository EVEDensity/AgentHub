-- 007: AgentNet — Decentralized Multi-Agent Collaboration Network
-- Sprint I (I1-I5) — Agent capabilities, tasks, DAG orchestration, spawns, shared memory.

-- ── I1: Agent Capability Manifest ─────────────────────────────────────
-- Self-declared capability registry published by each agent via heartbeat.

CREATE TABLE IF NOT EXISTS agentnet_capabilities (
    agent_id         TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL DEFAULT '',
    display_name     TEXT NOT NULL DEFAULT '',
    capabilities     TEXT[] DEFAULT '{}',
    preferred_tools  TEXT[] DEFAULT '{}',
    quality_score    REAL NOT NULL DEFAULT 0.8,
    current_load     INTEGER NOT NULL DEFAULT 0,
    max_concurrent   INTEGER NOT NULL DEFAULT 5,
    cost_per_task    REAL NOT NULL DEFAULT 0.01,
    status           TEXT NOT NULL DEFAULT 'idle',  -- idle, busy, overloaded, offline
    last_heartbeat   TIMESTAMPTZ NOT NULL DEFAULT now(),
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentnet_cap_status     ON agentnet_capabilities(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_agentnet_cap_capability ON agentnet_capabilities USING GIN(capabilities);
CREATE INDEX IF NOT EXISTS idx_agentnet_cap_heartbeat  ON agentnet_capabilities(last_heartbeat);

-- ── I2: AgentNet Tasks ────────────────────────────────────────────────
-- Task lifecycle: pending → assigned → running → completed / failed.

CREATE TABLE IF NOT EXISTS agentnet_tasks (
    task_id            TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL DEFAULT '',
    parent_task_id     TEXT,
    dag_id             TEXT,
    correlation_id     TEXT NOT NULL,
    category           TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    required_capability TEXT NOT NULL DEFAULT '',
    assigned_agent     TEXT,
    status             TEXT NOT NULL DEFAULT 'pending',  -- pending, assigned, running, completed, failed
    input              JSONB DEFAULT '{}',
    result             JSONB DEFAULT '{}',
    error_message      TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_at        TIMESTAMPTZ,
    completed_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agentnet_task_status   ON agentnet_tasks(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_agentnet_task_dag      ON agentnet_tasks(dag_id);
CREATE INDEX IF NOT EXISTS idx_agentnet_task_corr     ON agentnet_tasks(correlation_id);
CREATE INDEX IF NOT EXISTS idx_agentnet_task_agent    ON agentnet_tasks(assigned_agent);
CREATE INDEX IF NOT EXISTS idx_agentnet_task_parent   ON agentnet_tasks(parent_task_id);

-- ── I3: Dynamic DAG Orchestration ─────────────────────────────────────
-- DAG definitions with nodes and edges stored as JSONB for flexibility.

CREATE TABLE IF NOT EXISTS agentnet_dags (
    dag_id      TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL DEFAULT '',
    session_id  TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    strategy    TEXT NOT NULL DEFAULT 'capability-match',  -- round-robin, least-loaded, capability-match, cost-optimized
    status      TEXT NOT NULL DEFAULT 'created',           -- created, running, completed, failed, cancelled
    nodes       JSONB NOT NULL DEFAULT '[]',               -- array of DAGNode
    edges       JSONB NOT NULL DEFAULT '[]',               -- array of DAGEdge
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentnet_dag_status ON agentnet_dags(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_agentnet_dag_session ON agentnet_dags(session_id);

-- ── I4: Agent Spawn Registry ──────────────────────────────────────────
-- Tracks child agents spawned by parent agents.

CREATE TABLE IF NOT EXISTS agentnet_spawns (
    spawn_id     TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL DEFAULT '',
    parent_id    TEXT NOT NULL,
    child_id     TEXT NOT NULL,
    child_name   TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    capabilities TEXT[] DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'created',  -- created, running, completed, destroyed
    ttl_seconds  INTEGER NOT NULL DEFAULT 600,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agentnet_spawn_parent ON agentnet_spawns(parent_id);
CREATE INDEX IF NOT EXISTS idx_agentnet_spawn_child  ON agentnet_spawns(child_id);
CREATE INDEX IF NOT EXISTS idx_agentnet_spawn_status ON agentnet_spawns(tenant_id, status);

-- ── I2/I5: Shared Memory Channel ──────────────────────────────────────
-- Emergent inter-agent communication log.

CREATE TABLE IF NOT EXISTS agentnet_shared_memory (
    id         TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL DEFAULT '',
    agent_id   TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',
    intent     TEXT NOT NULL DEFAULT '',
    target     TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentnet_memory_agent  ON agentnet_shared_memory(agent_id);
CREATE INDEX IF NOT EXISTS idx_agentnet_memory_intent ON agentnet_shared_memory(tenant_id, intent);
CREATE INDEX IF NOT EXISTS idx_agentnet_memory_time   ON agentnet_shared_memory(tenant_id, created_at DESC);

-- ── I5: AgentNet Topology Snapshots (optional cache) ──────────────────
-- Cached topology snapshots for dashboard quick-load.

CREATE TABLE IF NOT EXISTS agentnet_topology_snapshots (
    id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id  TEXT NOT NULL DEFAULT '',
    nodes      JSONB NOT NULL DEFAULT '[]',
    edges      JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentnet_topo_tenant ON agentnet_topology_snapshots(tenant_id, created_at DESC);
