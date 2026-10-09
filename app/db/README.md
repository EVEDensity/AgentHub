# Database initialization and migrations

`app/db/` owns database connections, dialect translation, and startup schema
upgrades. Mission lifecycle and verification remain in the domain/services;
database initialization never invents successful work or resume fingerprints.

PostgreSQL's Alembic graph and runtime startup share the same SQL definitions.
Online Alembic commands explicitly select the installed `psycopg2` driver for
plain PostgreSQL DSNs; SQLAlchemy's changing default driver is not relied on.
The original checkpoint revision is `a6d0e1f2b3c4`, following Contract lineage
revision `f5c9d0e1a2b3`. Additive resume fields live in the separate
`b7e1f203c4d5` revision. Session compatibility follows at `c8f2a314d5e6`; Runner presence is the additive
head `d9a3b425e6f7`. Already present checkpoint fields are preserved.

SQLite schema version 5 upgrades versions 2, 3 and 4 in a transaction that contains
the missing-column additions and the schema marker. It does not replay seeds
or legacy DDL. Missing checkpoint tables and failed ALTERs fail startup without
advancing the marker. Fresh profiles install the same five optional fields.
Legacy rows retain NULL fingerprints and remain ineligible for strict resume.

`init_db.py` is the compatible bootstrap entry point and export facade.
`legacy_schema.py` holds fallback DDL, `legacy_seeds.py` holds shared bootstrap
defaults and the historical `now()` helper, and `legacy_migrations.py` holds
PostgreSQL compatibility upgrades for the legacy tables. Their SQL, defaults and
startup order are unchanged. `sqlite_initializer.py` owns the local bootstrap
and incremental schema transaction. Session compatibility adds explicit workspace ownership to the
legacy table and creates session event/confirmation storage on both backends.
Legacy session scope remains NULL; v1 access fails closed until trustworthy scope
is assigned explicitly. Timestamp serialization stays compatible with legacy
transports. See [ADR-0113](../../docs/architecture/decisions/0113-session-workspace-ownership-and-cursors.md).

`migrations/mission_control_plane.py` preserves all historical revision IDs and
upgrade/downgrade imports as a facade. Immutable SQL lives in small
`mission_schema`, `work_unit_schema`, `artifact_schema`, `decision_schema`,
`contract_schema`, `checkpoint_schema` and `session_schema` modules. Alembic,
runtime PostgreSQL startup and SQLite translation still consume the same SQL
tuples in the same order. Moving definitions does not revise migration history,
schema ownership, verification authority or rollback behavior.

The extraction reduces `init_db.py` from 824 to 93 physical lines and the
control-plane definition file from 1094 to 99. Each new module stays below
300 physical lines, and the moved Python functions stay within the new-code
complexity limit. The retired control-plane file-size exemption is removed;
other historical code debt remains visible in the full audit.

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
python -m pytest tests/integration/test_checkpoint_resume_postgres.py tests/integration/test_session_workspace_postgres.py -q
```

Runner presence is an expiring operational projection, not a lifecycle owner.
Version 4 upgrades only that projection without replaying seeds. Its DDL and
marker commit together; migration failure leaves the prior marker and data.
See `tests/persistence/test_runner_presence_migrations.py` and
`tests/integration/test_runner_presence_migrations_postgres.py`.
