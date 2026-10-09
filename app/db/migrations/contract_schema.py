"""Immutable historical contract SQL, shared by Alembic and runtime startup."""

CONTRACT_REVISION_BINDING_UPGRADE = (
    """
    ALTER TABLE missions
    ADD COLUMN IF NOT EXISTS contract_version INTEGER
        CHECK (contract_version >= 1)
    """,
    """
    UPDATE missions AS mission
    SET contract_version = contract.version
    FROM mission_contracts AS contract
    WHERE contract.id = mission.contract_id
      AND mission.contract_version IS NULL
    """,
    """
    DO $migration$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM missions WHERE contract_version IS NULL
        ) THEN
            RAISE EXCEPTION
                'cannot bind Mission to a missing Contract revision';
        END IF;
    END
    $migration$
    """,
    """
    DO $migration$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conrelid = 'mission_contracts'::regclass
              AND conname = 'mission_contracts_document_identity_check'
        ) THEN
            ALTER TABLE mission_contracts
            ADD CONSTRAINT mission_contracts_document_identity_check CHECK (
                jsonb_typeof(document) = 'object'
                AND document->>'id' = id
                AND document->>'version' = version::text
            );
        END IF;
    END
    $migration$
    """,
    """
    ALTER TABLE missions
    DROP CONSTRAINT IF EXISTS missions_contract_id_fkey
    """,
    """
    DO $migration$
    DECLARE primary_key_definition TEXT;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO primary_key_definition
        FROM pg_constraint
        WHERE conrelid = 'mission_contracts'::regclass
          AND conname = 'mission_contracts_pkey';

        IF primary_key_definition IS DISTINCT FROM 'PRIMARY KEY (id, version)' THEN
            ALTER TABLE mission_contracts
            DROP CONSTRAINT IF EXISTS mission_contracts_pkey;
            ALTER TABLE mission_contracts
            ADD CONSTRAINT mission_contracts_pkey PRIMARY KEY (id, version);
        END IF;
    END
    $migration$
    """,
    """
    ALTER TABLE missions
    ALTER COLUMN contract_version SET NOT NULL
    """,
    """
    DO $migration$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conrelid = 'missions'::regclass
              AND conname = 'missions_contract_revision_fkey'
        ) THEN
            ALTER TABLE missions
            ADD CONSTRAINT missions_contract_revision_fkey
                FOREIGN KEY (contract_id, contract_version)
                REFERENCES mission_contracts(id, version);
        END IF;
    END
    $migration$
    """,
)


CONTRACT_REVISION_BINDING_DOWNGRADE = (
    """
    DO $migration$
    BEGIN
        IF EXISTS (
            SELECT id
            FROM mission_contracts
            GROUP BY id
            HAVING count(*) > 1
        ) THEN
            RAISE EXCEPTION
                'cannot downgrade Contract revision binding with multiple revisions';
        END IF;
    END
    $migration$
    """,
    """
    ALTER TABLE missions
    DROP CONSTRAINT missions_contract_revision_fkey
    """,
    """
    ALTER TABLE mission_contracts
    DROP CONSTRAINT mission_contracts_pkey,
    ADD CONSTRAINT mission_contracts_pkey PRIMARY KEY (id)
    """,
    """
    ALTER TABLE missions
    ADD CONSTRAINT missions_contract_id_fkey
        FOREIGN KEY (contract_id) REFERENCES mission_contracts(id),
    DROP COLUMN contract_version
    """,
)


CONTRACT_LINEAGE_OWNERSHIP_UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS mission_contract_lineages (
        contract_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL CHECK (
            length(workspace_id) BETWEEN 1 AND 255
        ),
        UNIQUE (contract_id, workspace_id)
    )
    """,
    """
    DO $migration$
    BEGIN
        IF EXISTS (
            SELECT contract_id
            FROM missions
            GROUP BY contract_id
            HAVING count(DISTINCT workspace_id) > 1
        ) THEN
            RAISE EXCEPTION
                'cannot assign Contract lineage shared across workspaces';
        END IF;
    END
    $migration$
    """,
    """
    DO $migration$
    BEGIN
        IF EXISTS (
            SELECT contract.id
            FROM mission_contracts AS contract
            LEFT JOIN missions AS mission ON mission.contract_id = contract.id
            WHERE mission.id IS NULL
        ) THEN
            RAISE EXCEPTION
                'cannot assign orphan Contract lineage to a workspace';
        END IF;
    END
    $migration$
    """,
    """
    INSERT INTO mission_contract_lineages(contract_id, workspace_id)
    SELECT contract_id, min(workspace_id)
    FROM missions
    GROUP BY contract_id
    ON CONFLICT (contract_id) DO NOTHING
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mission_contract_lineages_workspace
    ON mission_contract_lineages(workspace_id, contract_id)
    """,
    """
    DO $migration$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conrelid = 'mission_contracts'::regclass
              AND conname = 'mission_contracts_lineage_fkey'
        ) THEN
            ALTER TABLE mission_contracts
            ADD CONSTRAINT mission_contracts_lineage_fkey
                FOREIGN KEY (id)
                REFERENCES mission_contract_lineages(contract_id);
        END IF;
    END
    $migration$
    """,
    """
    DO $migration$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conrelid = 'missions'::regclass
              AND conname = 'missions_contract_lineage_workspace_fkey'
        ) THEN
            ALTER TABLE missions
            ADD CONSTRAINT missions_contract_lineage_workspace_fkey
                FOREIGN KEY (contract_id, workspace_id)
                REFERENCES mission_contract_lineages(contract_id, workspace_id);
        END IF;
    END
    $migration$
    """,
)


CONTRACT_LINEAGE_OWNERSHIP_DOWNGRADE = (
    """
    ALTER TABLE missions
    DROP CONSTRAINT IF EXISTS missions_contract_lineage_workspace_fkey
    """,
    """
    ALTER TABLE mission_contracts
    DROP CONSTRAINT IF EXISTS mission_contracts_lineage_fkey
    """,
    "DROP TABLE IF EXISTS mission_contract_lineages",
)
