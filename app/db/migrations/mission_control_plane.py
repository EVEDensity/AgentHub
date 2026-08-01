"""Schema definition for the first Mission control-plane persistence slice."""

MISSION_CONTROL_PLANE_REVISION = "4f6d2a8c901b"
MISSION_CONTROL_PLANE_DOWN_REVISION = "c1a7d4e82b6f"

MISSION_CONTROL_PLANE_UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS mission_contracts (
        id TEXT PRIMARY KEY,
        version INTEGER NOT NULL CHECK (version >= 1),
        document JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS missions (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        title TEXT NOT NULL,
        objective TEXT NOT NULL,
        source JSONB NOT NULL,
        contract_id TEXT NOT NULL REFERENCES mission_contracts(id),
        status TEXT NOT NULL CHECK (
            status IN (
                'DRAFT', 'READY', 'RUNNING', 'VERIFYING', 'WAITING_DECISION',
                'SUCCEEDED', 'FAILED', 'CANCELLED'
            )
        ),
        plan_version INTEGER NOT NULL DEFAULT 0 CHECK (plan_version >= 0),
        created_by JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CHECK (updated_at >= created_at)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_missions_workspace_updated
    ON missions(workspace_id, updated_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_missions_status_updated
    ON missions(status, updated_at DESC)
    """,
)

MISSION_CONTROL_PLANE_DOWNGRADE = (
    "DROP TABLE IF EXISTS missions",
    "DROP TABLE IF EXISTS mission_contracts",
)
