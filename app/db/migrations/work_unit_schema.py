"""Immutable historical work unit SQL, shared by Alembic and runtime startup."""

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


A2A_SOURCE_MAPPING_UPGRADE = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_missions_a2a_external_task
    ON missions(workspace_id, (source->>'externalId'))
    WHERE source->>'type' = 'a2a' AND source ? 'externalId'
    """,
)


A2A_SOURCE_MAPPING_DOWNGRADE = (
    "DROP INDEX IF EXISTS uq_missions_a2a_external_task",
)


DELEGATION_PERSISTENCE_UPGRADE = (
    """
    ALTER TABLE work_units
    ADD COLUMN IF NOT EXISTS parent_work_unit_id TEXT REFERENCES work_units(id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_work_units_parent
    ON work_units(parent_work_unit_id)
    """,
)


DELEGATION_PERSISTENCE_DOWNGRADE = (
    "DROP INDEX IF EXISTS idx_work_units_parent",
    "ALTER TABLE work_units DROP COLUMN IF EXISTS parent_work_unit_id",
)


AGENT_BINDING_PERSISTENCE_UPGRADE = (
    """
    ALTER TABLE work_units
    ADD COLUMN IF NOT EXISTS assigned_agent_id TEXT
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_work_units_assigned_agent
    ON work_units(assigned_agent_id)
    """,
)


AGENT_BINDING_PERSISTENCE_DOWNGRADE = (
    "DROP INDEX IF EXISTS idx_work_units_assigned_agent",
    "ALTER TABLE work_units DROP COLUMN IF EXISTS assigned_agent_id",
)


AGENT_CATALOG_PROJECTION_UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS agent_catalog_bindings (
        scope_id TEXT NOT NULL CHECK (length(scope_id) BETWEEN 1 AND 255),
        agent_id TEXT NOT NULL CHECK (length(agent_id) BETWEEN 1 AND 255),
        adapter_type TEXT NOT NULL CHECK (
            adapter_type ~ '^[a-z][a-z0-9_-]{0,63}$'
        ),
        capabilities JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
            jsonb_typeof(capabilities) = 'array'
        ),
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        source_version INTEGER NOT NULL DEFAULT 1 CHECK (source_version >= 1),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (scope_id, agent_id)
    )
    """,
)


AGENT_CATALOG_PROJECTION_DOWNGRADE = (
    "DROP TABLE IF EXISTS agent_catalog_bindings",
)


A2A_INBOUND_SOURCE_MAPPING_UPGRADE = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_missions_a2a_inbound_external_task
    ON missions(workspace_id, (source->>'reference'), (source->>'externalId'))
    WHERE source->>'type' = 'a2a.inbound'
      AND source ? 'reference'
      AND source ? 'externalId'
    """,
)


A2A_INBOUND_SOURCE_MAPPING_DOWNGRADE = (
    "DROP INDEX IF EXISTS uq_missions_a2a_inbound_external_task",
)
