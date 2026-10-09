"""Historical compatibility DDL shared by PostgreSQL and SQLite bootstrap."""

_PG_DDL = [
    # ── Core tables ────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL,
        password_hash TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL DEFAULT 'group',
        participants TEXT NOT NULL DEFAULT '[]',
        active INTEGER NOT NULL DEFAULT 1,
        is_pinned INTEGER NOT NULL DEFAULT 0,
        last_message_at TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        owner_id TEXT NOT NULL DEFAULT '',
        visibility TEXT NOT NULL DEFAULT 'private'
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        sender TEXT NOT NULL,
        content TEXT NOT NULL,
        type TEXT NOT NULL DEFAULT 'text',
        fidelity_score REAL DEFAULT 0.95,
        symbolic_json TEXT DEFAULT '{}',
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS agent_registry (
        agent_id TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT '',
        domain TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'sleeping',
        adapter_type TEXT NOT NULL DEFAULT 'mock',
        base_model_name TEXT NOT NULL DEFAULT '',
        config TEXT NOT NULL DEFAULT '{}',
        risk_level TEXT NOT NULL DEFAULT 'L1',
        duty_note TEXT NOT NULL DEFAULT '',
        display_name TEXT NOT NULL DEFAULT '',
        avatar_url TEXT NOT NULL DEFAULT '',
        capability_tags TEXT NOT NULL DEFAULT '[]',
        base_url TEXT NOT NULL DEFAULT '',
        api_key TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (agent_id, user_id)
    )""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        dag_json TEXT NOT NULL,
        current_node_id TEXT,
        template_id INTEGER,
        agent_route_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS task_execution_history (
        id SERIAL PRIMARY KEY,
        task_type TEXT NOT NULL,
        assigned_agent TEXT NOT NULL,
        success BOOLEAN NOT NULL,
        duration_ms INTEGER,
        tool_calls_count INTEGER DEFAULT 0,
        retry_count INTEGER DEFAULT 0,
        error_type TEXT,
        session_id TEXT,
        created_at TEXT NOT NULL,
        memory_type TEXT NOT NULL DEFAULT 'episodic',
        memory_scope TEXT NOT NULL DEFAULT 'session',
        memory_source TEXT NOT NULL DEFAULT 'task_execution',
        memory_version INTEGER NOT NULL DEFAULT 1
    )""",
    """ALTER TABLE task_execution_history ADD COLUMN IF NOT EXISTS memory_type TEXT NOT NULL DEFAULT 'episodic'""",
    """ALTER TABLE task_execution_history ADD COLUMN IF NOT EXISTS memory_scope TEXT NOT NULL DEFAULT 'session'""",
    """ALTER TABLE task_execution_history ADD COLUMN IF NOT EXISTS memory_source TEXT NOT NULL DEFAULT 'task_execution'""",
    """ALTER TABLE task_execution_history ADD COLUMN IF NOT EXISTS memory_version INTEGER NOT NULL DEFAULT 1""",
    """CREATE INDEX IF NOT EXISTS idx_teh_agent_type ON task_execution_history(assigned_agent, task_type)""",
    """CREATE INDEX IF NOT EXISTS idx_teh_memory_type_scope ON task_execution_history(memory_type, memory_scope, session_id)""",
    """CREATE TABLE IF NOT EXISTS dag_templates (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        keywords TEXT NOT NULL,
        dag_json TEXT NOT NULL,
        usage_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS model_configs (
        id SERIAL PRIMARY KEY,
        provider TEXT NOT NULL,
        model_name TEXT NOT NULL,
        api_key TEXT NOT NULL DEFAULT '',
        api_key_hash TEXT NOT NULL DEFAULT '',
        base_url TEXT DEFAULT '',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS role_bindings (
        role TEXT PRIMARY KEY,
        model_config_id INTEGER NOT NULL,
        prompt TEXT DEFAULT '',
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS agent_routes (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        trigger_keywords TEXT NOT NULL DEFAULT '[]',
        nodes_json TEXT NOT NULL,
        edges_json TEXT NOT NULL DEFAULT '[]',
        is_default INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        version INTEGER NOT NULL DEFAULT 1,
        schema_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_routes_name_user ON agent_routes(name, user_id)""",
    """CREATE TABLE IF NOT EXISTS workflow_drafts (
        id SERIAL PRIMARY KEY,
        user_id TEXT NOT NULL,
        workflow_id INTEGER,
        draft_key TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL,
        base_version INTEGER NOT NULL DEFAULT 0,
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(user_id, draft_key)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_workflow_drafts_user_updated ON workflow_drafts(user_id, updated_at DESC)""",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        action TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        decision TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        timestamp TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS system_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    # ── Tool-calling infrastructure ────────────────────────────────
    """CREATE TABLE IF NOT EXISTS tool_definitions (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL,
        category TEXT NOT NULL,
        parameters_json TEXT NOT NULL,
        return_type TEXT NOT NULL,
        examples_json TEXT NOT NULL DEFAULT '[]',
        risk_level TEXT NOT NULL DEFAULT 'L1',
        handler_type TEXT NOT NULL DEFAULT 'builtin',
        handler_config TEXT NOT NULL DEFAULT '{}',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS agent_tool_bindings (
        agent_id TEXT NOT NULL,
        tool_id INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (agent_id, tool_id)
    )""",
    """CREATE TABLE IF NOT EXISTS tool_call_log (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        arguments_json TEXT NOT NULL,
        result_json TEXT NOT NULL DEFAULT '{}',
        success INTEGER NOT NULL DEFAULT 0,
        duration_ms REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tool_permission_rules (
        id SERIAL PRIMARY KEY,
        agent_id TEXT NOT NULL DEFAULT '*',
        tool_pattern TEXT NOT NULL,
        path_pattern TEXT NOT NULL DEFAULT '*',
        behavior TEXT NOT NULL DEFAULT 'ask',
        source TEXT NOT NULL DEFAULT 'user',
        priority INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tool_hook_configs (
        id SERIAL PRIMARY KEY,
        hook_name TEXT NOT NULL,
        tool_name TEXT,
        hook_type TEXT NOT NULL,
        config_json TEXT NOT NULL DEFAULT '{}',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        file_path TEXT NOT NULL,
        content TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    # ── Multi-user collaboration tables ───────────────────────────
    """CREATE TABLE IF NOT EXISTS session_members (
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role TEXT NOT NULL DEFAULT 'member',
        invited_by TEXT NOT NULL DEFAULT '',
        joined_at TEXT NOT NULL,
        PRIMARY KEY (session_id, user_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_sm_user ON session_members(user_id)""",
    """CREATE INDEX IF NOT EXISTS idx_sm_session ON session_members(session_id)""",
    """CREATE TABLE IF NOT EXISTS user_presence (
        user_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'online',
        last_heartbeat TEXT NOT NULL,
        PRIMARY KEY (user_id, session_id)
    )""",
    """CREATE TABLE IF NOT EXISTS user_settings (
        user_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, key)
    )""",
    # ── MCP alert infrastructure ────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS alert_rules (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        rule_type TEXT NOT NULL,
        condition_json TEXT NOT NULL DEFAULT '{}',
        severity TEXT NOT NULL DEFAULT 'warning',
        enabled INTEGER NOT NULL DEFAULT 1,
        notify_channels TEXT NOT NULL DEFAULT '["websocket"]',
        silence_window_seconds INTEGER NOT NULL DEFAULT 3600,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS alert_history (
        id TEXT PRIMARY KEY,
        rule_id INTEGER,
        rule_name TEXT NOT NULL,
        severity TEXT NOT NULL,
        message TEXT NOT NULL,
        context_json TEXT NOT NULL DEFAULT '{}',
        acknowledged INTEGER NOT NULL DEFAULT 0,
        acknowledged_by TEXT NOT NULL DEFAULT '',
        triggered_at TEXT NOT NULL,
        resolved_at TEXT NOT NULL DEFAULT ''
    )""",
    # ── Performance indexes (critical query paths) ────────────────────
    # These are created via CREATE INDEX IF NOT EXISTS so they are
    # idempotent — safe to run on every startup.
    #
    # messages(session_id, created_at) — every chat load / scroll-back
    #    query filters on session_id and orders by created_at.
    """CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id, created_at DESC)""",
    # messages(user_id, created_at) — per-user message history queries.
    """CREATE INDEX IF NOT EXISTS idx_messages_user_created ON messages(user_id, created_at DESC)""",
    # agent_registry(user_id) — MCP dashboard and agent listing filter
    #    by user_id.  The composite PK (agent_id, user_id) doesn't help
    #    queries that scan by user_id alone.
    """CREATE INDEX IF NOT EXISTS idx_agent_registry_user ON agent_registry(user_id)""",
    # agent_registry(status) — dashboard health rollup (count by status).
    """CREATE INDEX IF NOT EXISTS idx_agent_registry_status ON agent_registry(status)""",
    # tool_call_log(session_id, created_at) — tool analytics & audit.
    """CREATE INDEX IF NOT EXISTS idx_tool_call_log_session_created ON tool_call_log(session_id, created_at DESC)""",
    # tool_call_log(agent_id, created_at) — per-agent tool usage stats.
    """CREATE INDEX IF NOT EXISTS idx_tool_call_log_agent_created ON tool_call_log(agent_id, created_at DESC)""",
    # audit_log(timestamp) — recent events stream on MCP dashboard.
    """CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC)""",
]
