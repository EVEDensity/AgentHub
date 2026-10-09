# Database initialization and migrations

`app/db/` owns database connections, dialect translation, and startup schema
upgrades. Mission lifecycle and verification remain in the domain/services;
database initialization never invents successful work or resume fingerprints.

PostgreSQL's Alembic graph and runtime startup share the same SQL definitions.
The original checkpoint revision is `a6d0e1f2b3c4`, following Contract lineage
revision `f5c9d0e1a2b3`. Additive resume fields live in the separate
`b7e1f203c4d5` revision. Previously recorded `b7e1f203c4d5` databases stay at
that head; already present fields are preserved when upgrading an older head.

SQLite schema version 3 upgrades version 2 in a transaction that contains both
the missing-column additions and the schema marker. It does not replay seeds
or legacy DDL. Missing checkpoint tables and failed ALTERs fail startup without
advancing the marker. Fresh profiles install the same five optional fields.
Legacy rows retain NULL fingerprints and remain ineligible for strict resume.

See [ADR-0110](../../docs/architecture/decisions/0110-checkpoint-resume-storage-compatibility.md)
for compatibility and [the runbook](../../docs/operations/checkpoint-resume-migration.md)
for upgrade, rollback, and verification.

```powershell
python -m alembic heads
python -m pytest tests/persistence/test_migrations.py tests/persistence/test_checkpoint_migration_graph.py tests/persistence/test_checkpoint_resume_sqlite.py tests/contracts/test_execution_checkpoint_contract.py -q
```

Real PostgreSQL tests require `AGENTHUB_TEST_POSTGRES_DSN` pointing to a
disposable test database. They create/drop unique schemas and verify actual
Alembic DDL, rollback, runtime startup, and repository serialization:

```powershell
python -m pytest tests/integration/test_checkpoint_resume_postgres.py -q
```
