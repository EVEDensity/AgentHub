"""Immutable historical session SQL, shared by Alembic and runtime startup."""

SESSION_EVENTS_UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS session_events (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK (
            event_type IN (
                'member.joined', 'member.left',
                'message.created',
                'mention.detected',
                'rule.triggered',
                'mission.created', 'mission.completed',
                'decision.recorded'
            )
        ),
        actor_type TEXT NOT NULL CHECK (
            actor_type IN ('human', 'service', 'agent', 'adapter', 'runner', 'verifier')
        ),
        actor_id TEXT NOT NULL,
        actor_display_name TEXT NOT NULL DEFAULT '',
        payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_events_session_created
    ON session_events(session_id, created_at, id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_events_type
    ON session_events(session_id, event_type, created_at)
    """,
)


SESSION_EVENTS_DOWNGRADE = (
    "DROP INDEX IF EXISTS idx_session_events_type",
    "DROP INDEX IF EXISTS idx_session_events_session_created",
    "DROP TABLE IF EXISTS session_events",
)


SESSIONS_UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'ARCHIVED')),
        metadata JSONB CHECK (jsonb_typeof(metadata) = 'object'),
        created_by_type TEXT NOT NULL CHECK (
            created_by_type IN ('human', 'service', 'agent', 'adapter', 'runner', 'verifier')
        ),
        created_by_id TEXT NOT NULL,
        created_by_display_name TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_workspace
    ON sessions(workspace_id, created_at DESC)
    """,
)


SESSIONS_DOWNGRADE = (
    "DROP INDEX IF EXISTS idx_sessions_workspace",
    "DROP TABLE IF EXISTS sessions",
)


PENDING_CONFIRMATIONS_UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS pending_confirmations (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        workspace_id TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        rule_description TEXT NOT NULL DEFAULT '',
        action_kind TEXT NOT NULL CHECK (action_kind IN ('create_mission')),
        target_agent TEXT,
        objective_template TEXT,
        message TEXT NOT NULL,
        request_payload JSONB NOT NULL DEFAULT '{}',
        status TEXT NOT NULL CHECK (status IN ('PENDING', 'CONFIRMED', 'CANCELLED', 'EXPIRED')) DEFAULT 'PENDING',
        created_by_type TEXT NOT NULL CHECK (
            created_by_type IN ('human', 'service', 'agent', 'adapter', 'runner', 'verifier')
        ),
        created_by_id TEXT NOT NULL,
        created_by_display_name TEXT NOT NULL DEFAULT '',
        expires_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        resolved_at TIMESTAMPTZ
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pending_confirmations_workspace_status
    ON pending_confirmations(workspace_id, status, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pending_confirmations_session
    ON pending_confirmations(session_id, created_at DESC)
    """,
)


PENDING_CONFIRMATIONS_DOWNGRADE = (
    "DROP INDEX IF EXISTS idx_pending_confirmations_session",
    "DROP INDEX IF EXISTS idx_pending_confirmations_workspace_status",
    "DROP TABLE IF EXISTS pending_confirmations",
)
