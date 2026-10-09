# Checkpoint resume metadata migration

> Status: implemented
> Owner: execution maintainers
> Last reviewed: 2026-10-09
> Scope: PostgreSQL checkpoint revision and local SQLite version-2 profiles

## Prerequisites and persistence

Stop Runner/Mission Control processes and take a restorable database backup
before upgrading an existing deployment. Use the deployment's existing secret
manager for its DSN; never commit credentials or use a production DSN for tests.
This change adds five nullable metadata fields and keeps original checkpoint,
Mission, Artifact and Evidence records. It adds no ports or services.

## PostgreSQL upgrade and checks

`python -m alembic heads` must report only `b7e1f203c4d5`. Configure the existing
`DATABASE_URL` securely, then run `python -m alembic upgrade head`. The startup
migrator can also advance a supported older head with the shared SQL. An
already recorded `b7e1f203c4d5` remains unchanged.

Verify the schema before admitting work:

```sql
SELECT version_num FROM alembic_version;
SELECT column_name FROM information_schema.columns
WHERE table_schema = current_schema() AND table_name = 'execution_checkpoints'
  AND column_name IN ('resume_protocol_version', 'next_action', 'idempotency_key',
                     'workspace_revision', 'context_manifest_digest');
```

Expect the head above and all five columns. Historical rows retain NULL
metadata. Do not fill them using the current checkout or claim they are resumable.

## SQLite upgrade and checks

Normal local Mission Control startup upgrades schema version 2 to 3 without
replaying seeds. The five column additions and marker update share a transaction.
On failure, startup fails and version 2 remains; resolve the reported schema
problem before retrying. An absent checkpoint table is a failure, not readiness.

```sql
SELECT MAX(version) FROM schema_migrations;
PRAGMA table_info(execution_checkpoints);
```

Expect version 3 and the same five column names. Existing metadata values and
user data are retained. No change in readiness/health semantics is introduced.

## Rollback

Stop execution first. PostgreSQL can run `python -m alembic downgrade
a6d0e1f2b3c4`; this discards the five resume fields while keeping original rows.
Re-upgrading leaves those discarded values NULL. Preserve a backup if their
metadata must be recovered. Strict resume must refuse these legacy checkpoints.

SQLite rollback restores the pre-upgrade backup with all processes stopped.
Do not merely lower `schema_migrations` or edit fingerprints and ToolReceipts.

## Verification

Run the contract and persistence tests listed in `app/db/README.md`. Set
`AGENTHUB_TEST_POSTGRES_DSN` to a dedicated disposable database for:

```powershell
python -m pytest tests/integration/test_checkpoint_resume_postgres.py -q
```

The tests create and drop only their uniquely named schemas. The checked-in
`Checkpoint migration compatibility` workflow supplies its own PostgreSQL
service. Successful schema tests do not certify real-provider, physical TTY,
distributed SSE or full crash-resume behavior.
