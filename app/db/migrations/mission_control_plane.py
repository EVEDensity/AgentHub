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
AGENT_CATALOG_PROJECTION_REVISION = "f9c3d4e5a6b7"
AGENT_CATALOG_PROJECTION_DOWN_REVISION = AGENT_BINDING_PERSISTENCE_REVISION
A2A_INBOUND_SOURCE_MAPPING_REVISION = "a0d4e5f6b7c8"
A2A_INBOUND_SOURCE_MAPPING_DOWN_REVISION = AGENT_CATALOG_PROJECTION_REVISION
DECISION_PERSISTENCE_REVISION = "b1e5f6a7c8d9"
DECISION_PERSISTENCE_DOWN_REVISION = A2A_INBOUND_SOURCE_MAPPING_REVISION
DECISION_EXPIRY_REVISION = "c2f6a7b8d9e0"
DECISION_EXPIRY_DOWN_REVISION = DECISION_PERSISTENCE_REVISION
ARTIFACT_TABLE_OWNERSHIP_REVISION = "d3a7b8c9e0f1"
ARTIFACT_TABLE_OWNERSHIP_DOWN_REVISION = DECISION_EXPIRY_REVISION

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

MISSION_ARTIFACT_TABLE_UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS mission_artifacts (
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
    CREATE INDEX IF NOT EXISTS idx_mission_artifacts_mission_created
    ON mission_artifacts(mission_id, created_at, id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mission_artifacts_work_unit_attempt
    ON mission_artifacts(work_unit_id, attempt, id)
    """,
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
    *MISSION_ARTIFACT_TABLE_UPGRADE,
)

ARTIFACT_PERSISTENCE_DOWNGRADE = (
    "DROP TABLE IF EXISTS mission_artifacts",
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

DECISION_PERSISTENCE_UPGRADE = (
    """
    ALTER TABLE mission_events
    DROP CONSTRAINT IF EXISTS mission_events_aggregate_type_check
    """,
    """
    ALTER TABLE mission_events
    ADD CONSTRAINT mission_events_aggregate_type_check CHECK (
        aggregate_type IN (
            'mission', 'mission_contract', 'work_unit', 'artifact', 'evidence',
            'decision'
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL REFERENCES missions(id),
        work_unit_id TEXT NOT NULL REFERENCES work_units(id),
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        context_digest TEXT NOT NULL CHECK (
            context_digest ~ '^sha256:[a-fA-F0-9]{64}$'
        ),
        reason_code TEXT NOT NULL CHECK (
            reason_code IN (
                'no_applicable_policy', 'ambiguous_policy',
                'invalid_configuration', 'unsupported_evaluator',
                'artifact_requirements_not_met'
            )
        ),
        criterion_ids JSONB NOT NULL CHECK (jsonb_typeof(criterion_ids) = 'array'),
        options JSONB NOT NULL CHECK (jsonb_typeof(options) = 'array'),
        recommended_option TEXT NOT NULL CHECK (
            recommended_option IN ('RETRY_WORK_UNIT', 'FAIL_MISSION')
        ),
        risk_summary TEXT NOT NULL CHECK (length(risk_summary) BETWEEN 1 AND 2000),
        status TEXT NOT NULL CHECK (
            status IN ('PENDING', 'RESOLVED', 'CANCELLED')
        ),
        version INTEGER NOT NULL CHECK (version >= 1),
        requested_by JSONB NOT NULL CHECK (jsonb_typeof(requested_by) = 'object'),
        requested_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ,
        resolution TEXT CHECK (
            resolution IS NULL
            OR resolution IN ('RETRY_WORK_UNIT', 'FAIL_MISSION')
        ),
        rationale TEXT CHECK (
            rationale IS NULL OR length(rationale) BETWEEN 1 AND 10000
        ),
        resolved_by JSONB CHECK (
            resolved_by IS NULL OR jsonb_typeof(resolved_by) = 'object'
        ),
        resolved_at TIMESTAMPTZ,
        UNIQUE (work_unit_id, attempt, context_digest),
        CHECK (expires_at IS NULL OR expires_at > requested_at),
        CHECK (
            (
                status = 'PENDING' AND version = 1
                AND resolution IS NULL AND rationale IS NULL
                AND resolved_by IS NULL AND resolved_at IS NULL
            )
            OR (
                status = 'RESOLVED' AND version >= 2
                AND resolution IS NOT NULL AND rationale IS NOT NULL
                AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL
                AND resolved_at >= requested_at
            )
            OR (
                status = 'CANCELLED' AND version >= 2
                AND resolution IS NULL AND rationale IS NOT NULL
                AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL
                AND resolved_at >= requested_at
            )
        )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_decisions_mission_status_requested
    ON decisions(mission_id, status, requested_at, id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_decisions_work_unit_attempt
    ON decisions(work_unit_id, attempt, requested_at, id)
    """,
)

DECISION_PERSISTENCE_DOWNGRADE = (
    "DROP TABLE IF EXISTS decisions",
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
)

DECISION_EXPIRY_UPGRADE = (
    """
    DO $migration$
    DECLARE constraint_name TEXT;
    BEGIN
        SELECT conname INTO constraint_name
        FROM pg_constraint
        WHERE conrelid = 'decisions'::regclass
          AND contype = 'c'
          AND cardinality(conkey) = 1
          AND pg_get_constraintdef(oid) LIKE '%PENDING%'
          AND pg_get_constraintdef(oid) LIKE '%RESOLVED%'
          AND pg_get_constraintdef(oid) LIKE '%CANCELLED%'
        LIMIT 1;
        IF constraint_name IS NULL THEN
            RAISE EXCEPTION 'decision status constraint not found';
        END IF;
        EXECUTE format(
            $constraint$
            ALTER TABLE decisions
            DROP CONSTRAINT %I,
            ADD CONSTRAINT decisions_status_check CHECK (
                status IN ('PENDING', 'RESOLVED', 'CANCELLED', 'EXPIRED')
            )
            $constraint$,
            constraint_name
        );
    END
    $migration$
    """,
    """
    DO $migration$
    DECLARE constraint_name TEXT;
    BEGIN
        SELECT conname INTO constraint_name
        FROM pg_constraint
        WHERE conrelid = 'decisions'::regclass
          AND contype = 'c'
          AND cardinality(conkey) > 1
          AND pg_get_constraintdef(oid) LIKE '%status%'
          AND pg_get_constraintdef(oid) LIKE '%resolved_at%'
          AND pg_get_constraintdef(oid) LIKE '%PENDING%'
        LIMIT 1;
        IF constraint_name IS NULL THEN
            RAISE EXCEPTION 'decision lifecycle constraint not found';
        END IF;
        EXECUTE format(
            $constraint$
            ALTER TABLE decisions
            DROP CONSTRAINT %I,
            ADD CONSTRAINT decisions_lifecycle_check CHECK (
                (
                    status = 'PENDING' AND version = 1
                    AND resolution IS NULL AND rationale IS NULL
                    AND resolved_by IS NULL AND resolved_at IS NULL
                )
                OR (
                    status = 'RESOLVED' AND version >= 2
                    AND resolution IS NOT NULL AND rationale IS NOT NULL
                    AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL
                    AND resolved_at >= requested_at
                )
                OR (
                    status = 'CANCELLED' AND version >= 2
                    AND resolution IS NULL AND rationale IS NOT NULL
                    AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL
                    AND resolved_at >= requested_at
                )
                OR (
                    status = 'EXPIRED' AND version >= 2
                    AND expires_at IS NOT NULL AND resolution IS NULL
                    AND rationale IS NOT NULL AND resolved_by IS NOT NULL
                    AND resolved_at IS NOT NULL AND resolved_at >= expires_at
                )
            )
            $constraint$,
            constraint_name
        );
    END
    $migration$
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_decisions_pending_expiry
    ON decisions(expires_at, id)
    WHERE status = 'PENDING' AND expires_at IS NOT NULL
    """,
)

DECISION_EXPIRY_DOWNGRADE = (
    """
    ALTER TABLE decisions
    DROP CONSTRAINT IF EXISTS decisions_lifecycle_check,
    DROP CONSTRAINT IF EXISTS decisions_status_check,
    ADD CONSTRAINT decisions_status_check CHECK (
        status IN ('PENDING', 'RESOLVED', 'CANCELLED')
    ),
    ADD CONSTRAINT decisions_lifecycle_check CHECK (
        (
            status = 'PENDING' AND version = 1
            AND resolution IS NULL AND rationale IS NULL
            AND resolved_by IS NULL AND resolved_at IS NULL
        )
        OR (
            status = 'RESOLVED' AND version >= 2
            AND resolution IS NOT NULL AND rationale IS NOT NULL
            AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL
            AND resolved_at >= requested_at
        )
        OR (
            status = 'CANCELLED' AND version >= 2
            AND resolution IS NULL AND rationale IS NOT NULL
            AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL
            AND resolved_at >= requested_at
        )
    )
    """,
    "DROP INDEX IF EXISTS idx_decisions_pending_expiry",
)

ARTIFACT_TABLE_OWNERSHIP_UPGRADE = (
    """
    DO $migration$
    DECLARE
        legacy_table REGCLASS := to_regclass('artifacts');
        mission_table REGCLASS := to_regclass('mission_artifacts');
        artifacts_is_mission_owned BOOLEAN := FALSE;
    BEGIN
        IF legacy_table IS NOT NULL THEN
            SELECT EXISTS (
                SELECT 1
                FROM pg_attribute
                WHERE attrelid = legacy_table
                  AND attname IN ('mission_id', 'work_unit_id')
                  AND NOT attisdropped
                GROUP BY attrelid
                HAVING count(*) = 2
            ) INTO artifacts_is_mission_owned;
        END IF;

        IF mission_table IS NOT NULL AND artifacts_is_mission_owned THEN
            RAISE EXCEPTION
                'ambiguous Artifact ownership: both Mission Artifact tables exist';
        END IF;

        IF mission_table IS NULL AND artifacts_is_mission_owned THEN
            ALTER TABLE artifacts RENAME TO mission_artifacts;
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'mission_artifacts'::regclass
                  AND conname = 'artifacts_pkey'
            ) THEN
                ALTER TABLE mission_artifacts
                RENAME CONSTRAINT artifacts_pkey TO mission_artifacts_pkey;
            END IF;
            IF to_regclass('idx_artifacts_mission_created') IS NOT NULL THEN
                ALTER INDEX idx_artifacts_mission_created
                RENAME TO idx_mission_artifacts_mission_created;
            END IF;
            IF to_regclass('idx_artifacts_work_unit_attempt') IS NOT NULL THEN
                ALTER INDEX idx_artifacts_work_unit_attempt
                RENAME TO idx_mission_artifacts_work_unit_attempt;
            END IF;
        END IF;
    END
    $migration$
    """,
    *MISSION_ARTIFACT_TABLE_UPGRADE,
)

ARTIFACT_TABLE_OWNERSHIP_DOWNGRADE = (
    """
    DO $migration$
    BEGIN
        IF to_regclass('artifacts') IS NULL
           AND to_regclass('mission_artifacts') IS NOT NULL THEN
            ALTER TABLE mission_artifacts RENAME TO artifacts;
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'artifacts'::regclass
                  AND conname = 'mission_artifacts_pkey'
            ) THEN
                ALTER TABLE artifacts
                RENAME CONSTRAINT mission_artifacts_pkey TO artifacts_pkey;
            END IF;
            IF to_regclass('idx_mission_artifacts_mission_created') IS NOT NULL THEN
                ALTER INDEX idx_mission_artifacts_mission_created
                RENAME TO idx_artifacts_mission_created;
            END IF;
            IF to_regclass('idx_mission_artifacts_work_unit_attempt') IS NOT NULL THEN
                ALTER INDEX idx_mission_artifacts_work_unit_attempt
                RENAME TO idx_artifacts_work_unit_attempt;
            END IF;
        END IF;
    END
    $migration$
    """,
)
