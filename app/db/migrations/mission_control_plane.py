"""Schema definition for the first Mission control-plane persistence slice."""

MISSION_CONTROL_PLANE_REVISION = "4f6d2a8c901b"
MISSION_CONTROL_PLANE_DOWN_REVISION = "c1a7d4e82b6f"
MISSION_EVENT_LEDGER_REVISION = "8b7c3d9e0a12"
MISSION_EVENT_LEDGER_DOWN_REVISION = MISSION_CONTROL_PLANE_REVISION
WORK_UNIT_PERSISTENCE_REVISION = "9c8d4e0f1b23"
WORK_UNIT_PERSISTENCE_DOWN_REVISION = MISSION_EVENT_LEDGER_REVISION
A2A_SOURCE_MAPPING_REVISION = "a47e5f102c34"
A2A_SOURCE_MAPPING_DOWN_REVISION = WORK_UNIT_PERSISTENCE_REVISION
ARTIFACT_PERSISTENCE_REVISION = "b58f6a213d45"
ARTIFACT_PERSISTENCE_DOWN_REVISION = A2A_SOURCE_MAPPING_REVISION
EVIDENCE_PROJECTION_REVISION = "c69e7b324f56"
EVIDENCE_PROJECTION_DOWN_REVISION = ARTIFACT_PERSISTENCE_REVISION
DELEGATION_PERSISTENCE_REVISION = "d7a1b2c3e4f5"
DELEGATION_PERSISTENCE_DOWN_REVISION = EVIDENCE_PROJECTION_REVISION
AGENT_BINDING_PERSISTENCE_REVISION = "e8b2c3d4f5a6"
AGENT_BINDING_PERSISTENCE_DOWN_REVISION = DELEGATION_PERSISTENCE_REVISION

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

ARTIFACT_PERSISTENCE_UPGRADE = (
    """
    ALTER TABLE mission_events
    DROP CONSTRAINT IF EXISTS mission_events_aggregate_type_check
    """,
    """
    ALTER TABLE mission_events
    ADD CONSTRAINT mission_events_aggregate_type_check CHECK (
        aggregate_type IN (
            'mission', 'mission_contract', 'work_unit', 'artifact', 'evidence'
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL REFERENCES missions(id),
        work_unit_id TEXT NOT NULL REFERENCES work_units(id),
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        kind TEXT NOT NULL CHECK (
            kind IN (
                'diff', 'commit', 'file', 'log', 'report', 'test-result',
                'build', 'pull-request'
            )
        ),
        digest TEXT NOT NULL CHECK (digest ~ '^sha256:[a-fA-F0-9]{64}$'),
        content_address TEXT NOT NULL,
        media_type TEXT NOT NULL,
        size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
        source_repository TEXT,
        base_commit TEXT CHECK (
            base_commit IS NULL OR base_commit ~ '^[a-fA-F0-9]{7,64}$'
        ),
        retention TEXT NOT NULL CHECK (
            retention IN ('ephemeral', 'mission', 'standard', 'legal-hold')
        ),
        sensitivity TEXT NOT NULL CHECK (
            sensitivity IN ('public', 'internal', 'confidential', 'restricted')
        ),
        created_by JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_artifacts_mission_created
    ON artifacts(mission_id, created_at, id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_artifacts_work_unit_attempt
    ON artifacts(work_unit_id, attempt, id)
    """,
)

ARTIFACT_PERSISTENCE_DOWNGRADE = (
    "DROP TABLE IF EXISTS artifacts",
    """
    ALTER TABLE mission_events
    DROP CONSTRAINT IF EXISTS mission_events_aggregate_type_check
    """,
    """
    ALTER TABLE mission_events
    ADD CONSTRAINT mission_events_aggregate_type_check CHECK (
        aggregate_type IN ('mission', 'mission_contract', 'work_unit', 'evidence')
    )
    """,
)

EVIDENCE_PROJECTION_UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS evidence (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL REFERENCES missions(id),
        work_unit_id TEXT REFERENCES work_units(id),
        criterion_id TEXT NOT NULL CHECK (
            length(criterion_id) BETWEEN 1 AND 255
        ),
        verifier JSONB NOT NULL CHECK (jsonb_typeof(verifier) = 'object'),
        verdict TEXT NOT NULL CHECK (
            verdict IN ('PASS', 'FAIL', 'INCONCLUSIVE')
        ),
        artifact_refs JSONB NOT NULL CHECK (
            jsonb_typeof(artifact_refs) = 'array'
        ),
        summary TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 10000),
        generated_at TIMESTAMPTZ NOT NULL,
        integrity_hash TEXT NOT NULL CHECK (
            integrity_hash ~ '^sha256:[a-fA-F0-9]{64}$'
        )
    )
    """,
    """
    INSERT INTO evidence(
        id, mission_id, work_unit_id, criterion_id, verifier, verdict,
        artifact_refs, summary, generated_at, integrity_hash
    )
    SELECT
        payload->>'id',
        payload->>'missionId',
        NULLIF(payload->>'workUnitId', ''),
        payload->>'criterionId',
        payload->'verifier',
        payload->>'verdict',
        payload->'artifactRefs',
        payload->>'summary',
        (payload->>'generatedAt')::timestamptz,
        payload->>'integrityHash'
    FROM mission_events
    WHERE aggregate_type = 'evidence'
      AND event_type = 'evidence.lifecycle.recorded'
    ON CONFLICT (id) DO NOTHING
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evidence_mission_generated
    ON evidence(mission_id, generated_at, id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evidence_work_unit_criterion
    ON evidence(work_unit_id, criterion_id, generated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evidence_mission_verdict_criterion
    ON evidence(mission_id, verdict, criterion_id)
    """,
)

EVIDENCE_PROJECTION_DOWNGRADE = ("DROP TABLE IF EXISTS evidence",)

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
