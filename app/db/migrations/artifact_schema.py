"""Immutable historical artifact SQL, shared by Alembic and runtime startup."""

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
