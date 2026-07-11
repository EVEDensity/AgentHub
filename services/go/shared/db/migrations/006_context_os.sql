-- 006: ContextOS — Unified Context Engine
-- Sprint H1/H2 — 4-layer memory (L0-L3), entity graph, sleep compression.

-- ── L1: Episodic Memory — session-level context segments ─────────────
-- Stores compressed checkpoints and summaries per session.
CREATE TABLE IF NOT EXISTS platform_context_segments (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    segment_type    TEXT NOT NULL DEFAULT 'summary',  -- summary, checkpoint, entity_extract
    title           TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL DEFAULT '',
    token_count     INTEGER NOT NULL DEFAULT 0,
    source_sequence_start BIGINT NOT NULL DEFAULT 0,
    source_sequence_end   BIGINT NOT NULL DEFAULT 0,
    source_message_count  INTEGER NOT NULL DEFAULT 0,
    entities        TEXT[] DEFAULT '{}',              -- extracted entity IDs
    metadata        JSONB DEFAULT '{}',
    compressed_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_ctx_seg_tenant   ON platform_context_segments(tenant_id);
CREATE INDEX IF NOT EXISTS idx_platform_ctx_seg_session  ON platform_context_segments(tenant_id, session_id);
CREATE INDEX IF NOT EXISTS idx_platform_ctx_seg_type     ON platform_context_segments(tenant_id, segment_type);

-- ── L3: Procedural Memory — entity-relation graph model ─────────────
-- Lightweight graph stored in PG using recursive CTE queries instead of Neo4j.

CREATE TABLE IF NOT EXISTS platform_entities (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    entity_type  TEXT NOT NULL DEFAULT 'concept',  -- user, agent, session, tool, document, concept, project
    name         TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    properties   JSONB DEFAULT '{}',               -- flexible key-value attributes
    source       TEXT NOT NULL DEFAULT '',          -- where this entity was extracted from
    confidence   REAL NOT NULL DEFAULT 1.0,         -- extraction confidence (0-1)
    last_seen_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_entities_tenant ON platform_entities(tenant_id);
CREATE INDEX IF NOT EXISTS idx_platform_entities_type   ON platform_entities(tenant_id, entity_type);
CREATE INDEX IF NOT EXISTS idx_platform_entities_name   ON platform_entities(tenant_id, name);

CREATE TABLE IF NOT EXISTS platform_relations (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    subject_id    TEXT NOT NULL REFERENCES platform_entities(id) ON DELETE CASCADE,
    predicate     TEXT NOT NULL,                     -- USED, CREATED, MENTIONS, RELATED_TO, EVOLVED_FROM, BELONGS_TO, DEPENDS_ON
    object_id     TEXT NOT NULL REFERENCES platform_entities(id) ON DELETE CASCADE,
    weight        REAL NOT NULL DEFAULT 1.0,         -- relationship strength
    evidence      TEXT NOT NULL DEFAULT '',          -- why this relation exists (source message/event)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_rel_tenant   ON platform_relations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_platform_rel_subject  ON platform_relations(subject_id);
CREATE INDEX IF NOT EXISTS idx_platform_rel_object   ON platform_relations(object_id);
CREATE INDEX IF NOT EXISTS idx_platform_rel_pred     ON platform_relations(tenant_id, predicate);

-- ── L0/L1: Sleep compression log ────────────────────────────────────
-- Records every compression run for dashboard history.

CREATE TABLE IF NOT EXISTS platform_compression_runs (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running',   -- running, completed, failed
    sessions_scanned   INTEGER NOT NULL DEFAULT 0,
    sessions_compressed INTEGER NOT NULL DEFAULT 0,
    messages_processed  BIGINT NOT NULL DEFAULT 0,
    tokens_before    BIGINT NOT NULL DEFAULT 0,
    tokens_after     BIGINT NOT NULL DEFAULT 0,
    entities_extracted  INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_comp_run_tenant ON platform_compression_runs(tenant_id);

-- ── Memory strategy decisions log ───────────────────────────────────
-- Records LLM memory strategy decisions (ADD/UPDATE/DELETE/NOOP).

CREATE TABLE IF NOT EXISTS platform_memory_decisions (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    entity_id       TEXT,                              -- the entity being decided on
    decision        TEXT NOT NULL,                      -- ADD, UPDATE, DELETE, NOOP
    existing_memory TEXT NOT NULL DEFAULT '',           -- existing memory snapshot
    new_information TEXT NOT NULL DEFAULT '',           -- new information received
    reasoning       TEXT NOT NULL DEFAULT '',           -- LLM's reasoning for decision
    similarity_score REAL,                              -- semantic similarity between existing/new
    conflict_detected BOOLEAN NOT NULL DEFAULT false,
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_mem_dec_tenant ON platform_memory_decisions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_platform_mem_dec_entity ON platform_memory_decisions(entity_id);
