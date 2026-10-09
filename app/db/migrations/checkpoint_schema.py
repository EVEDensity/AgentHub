"""Immutable historical checkpoint SQL, shared by Alembic and runtime startup."""

EXECUTION_CHECKPOINT_UPGRADE = (
    """
    DO $migration$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conrelid = 'work_units'::regclass
              AND conname = 'work_units_mission_identity_key'
        ) THEN
            ALTER TABLE work_units
            ADD CONSTRAINT work_units_mission_identity_key
                UNIQUE (id, mission_id);
        END IF;
    END
    $migration$
    """,
    """
    CREATE TABLE IF NOT EXISTS execution_checkpoints (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL REFERENCES missions(id),
        work_unit_id TEXT NOT NULL,
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        phase TEXT NOT NULL CHECK (
            phase IN (
                'harness.execution.started',
                'harness.iteration.started',
                'harness.model.started',
                'harness.model.completed',
                'harness.tool.started',
                'harness.tool.completed',
                'harness.budget.exhausted',
                'harness.execution.completed',
                'harness.execution.failed'
            )
        ),
        iteration INTEGER NOT NULL CHECK (iteration >= 0),
        tool_calls INTEGER NOT NULL CHECK (tool_calls >= 0),
        prompt_tokens INTEGER NOT NULL CHECK (prompt_tokens >= 0),
        completion_tokens INTEGER NOT NULL CHECK (completion_tokens >= 0),
        model_cost DOUBLE PRECISION NOT NULL CHECK (
            model_cost >= 0
            AND model_cost < 'Infinity'::double precision
        ),
        terminal BOOLEAN NOT NULL,
        failure_reason TEXT CHECK (
            failure_reason IS NULL
            OR length(failure_reason) BETWEEN 1 AND 2000
        ),
        state_digest TEXT NOT NULL CHECK (
            state_digest ~ '^sha256:[a-fA-F0-9]{64}$'
        ),
        created_by JSONB NOT NULL CHECK (jsonb_typeof(created_by) = 'object'),
        created_at TIMESTAMPTZ NOT NULL,
        FOREIGN KEY (work_unit_id, mission_id)
            REFERENCES work_units(id, mission_id),
        UNIQUE (work_unit_id, attempt, sequence),
        CHECK (
            (
                terminal
                AND phase = 'harness.execution.failed'
                AND failure_reason IS NOT NULL
            )
            OR (
                terminal
                AND phase = 'harness.execution.completed'
                AND failure_reason IS NULL
            )
            OR (
                NOT terminal
                AND phase NOT IN (
                    'harness.execution.completed',
                    'harness.execution.failed'
                )
                AND failure_reason IS NULL
            )
        )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_execution_checkpoints_mission_created
    ON execution_checkpoints(mission_id, created_at, id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_execution_checkpoints_work_unit_attempt
    ON execution_checkpoints(work_unit_id, attempt, sequence)
    """,
)


EXECUTION_CHECKPOINT_DOWNGRADE = (
    "DROP TABLE IF EXISTS execution_checkpoints",
    """
    ALTER TABLE work_units
    DROP CONSTRAINT IF EXISTS work_units_mission_identity_key
    """,
)
