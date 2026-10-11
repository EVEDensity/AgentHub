# Checkpoint resume metadata migration

> Status: implemented
> Owner: execution maintainers
> Last reviewed: 2026-10-10
> Scope: PostgreSQL checkpoint revision and local SQLite version-2 profiles

## Prerequisites and persistence

Stop Runner/Mission Control processes and take a restorable database backup
before upgrading an existing deployment. Use the deployment's existing secret
manager for its DSN; never commit credentials or use a production DSN for tests.
This change adds five nullable metadata fields and keeps original checkpoint,
Mission, Artifact and Evidence records. It adds no ports or services.

## PostgreSQL upgrade and checks

`python -m alembic heads` must report only `d9a3b425e6f7`. Configure the existing
`DATABASE_URL` securely, then run `python -m alembic upgrade head`. The startup
migrator can also advance a supported older head with the shared SQL. An
already recorded `b7e1f203c4d5` advances through the additive session compatibility
revision and Runner presence revision while preserving checkpoint metadata.

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

Normal local Mission Control startup upgrades schema versions 2, 3 or 4 to 5 without
replaying seeds. The five checkpoint columns, session compatibility additions,
Runner presence and marker update share a transaction. Version 3 adds session
storage and presence; version 4 adds presence only.
On failure, startup fails and version 2 remains; resolve the reported schema
problem before retrying. An absent checkpoint table is a failure, not readiness.

```sql
SELECT MAX(version) FROM schema_migrations;
PRAGMA table_info(execution_checkpoints);
```

Expect version 5 and the same five column names. Existing metadata values and
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

The tests create and drop only their uniquely named schemas. The consolidated
`CI` workflow supplies its own PostgreSQL service and runs all integration tests.
Successful schema tests do not certify real-provider, physical TTY,
distributed SSE or full crash-resume behavior.

The separate protocol-v2 private journal and actual process kill/restart tests
are described in [ADR-0115](../architecture/decisions/0115-private-runner-resume-images.md).
Schema compatibility alone cannot supply a missing private image. Never edit a
digest or successful receipt to make an old checkpoint appear recoverable.
