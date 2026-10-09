"""Immutable historical decision SQL, shared by Alembic and runtime startup."""

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
