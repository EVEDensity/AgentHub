"""Schema definition for the first Mission control-plane persistence slice."""

MISSION_CONTROL_PLANE_REVISION = "4f6d2a8c901b"
MISSION_CONTROL_PLANE_DOWN_REVISION = "c1a7d4e82b6f"
MISSION_EVENT_LEDGER_REVISION = "8b7c3d9e0a12"
MISSION_EVENT_LEDGER_DOWN_REVISION = MISSION_CONTROL_PLANE_REVISION
WORK_UNIT_PERSISTENCE_REVISION = "9c8d4e0f1b23"
WORK_UNIT_PERSISTENCE_DOWN_REVISION = MISSION_EVENT_LEDGER_REVISION

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

MISSION_EVENT_LEDGER_UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS mission_events (
        event_id TEXT PRIMARY KEY,
        aggregate_type TEXT NOT NULL CHECK (
            aggregate_type IN ('mission', 'mission_contract', 'work_unit', 'evidence')
        ),
        aggregate_id TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        event_type TEXT NOT NULL,
        actor JSONB NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL,
        correlation_id TEXT NOT NULL,
        causation_id TEXT,
        payload JSONB NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (aggregate_type, aggregate_id, sequence)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mission_events_aggregate_sequence
    ON mission_events(aggregate_type, aggregate_id, sequence)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mission_events_correlation
    ON mission_events(correlation_id, occurred_at)
    """,
)

MISSION_EVENT_LEDGER_DOWNGRADE = ("DROP TABLE IF EXISTS mission_events",)

WORK_UNIT_PERSISTENCE_UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS work_units (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL REFERENCES missions(id),
        kind TEXT NOT NULL,
        dependencies JSONB NOT NULL,
        input_refs JSONB NOT NULL,
        expected_outputs JSONB NOT NULL,
        required_capabilities JSONB NOT NULL,
        assigned_adapter TEXT,
        status TEXT NOT NULL CHECK (
            status IN (
                'PENDING', 'LEASED', 'RUNNING', 'VERIFYING', 'WAITING',
                'RETRYING', 'SUCCEEDED', 'FAILED', 'CANCELLED'
            )
        ),
        attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
        lease JSONB,
        CHECK (jsonb_typeof(dependencies) = 'array'),
        CHECK (jsonb_typeof(input_refs) = 'array'),
        CHECK (jsonb_typeof(expected_outputs) = 'array'),
        CHECK (jsonb_typeof(required_capabilities) = 'array'),
        CHECK (
            (status IN ('LEASED', 'RUNNING') AND lease IS NOT NULL)
            OR (status NOT IN ('LEASED', 'RUNNING') AND lease IS NULL)
        )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_work_units_mission_status
    ON work_units(mission_id, status, id)
    """,
)

WORK_UNIT_PERSISTENCE_DOWNGRADE = ("DROP TABLE IF EXISTS work_units",)
