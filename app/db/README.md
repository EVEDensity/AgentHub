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
revision `d9a3b425e6f7`. Mission budget ledger follows at the additive head
`e0b4c536f708`. Already present checkpoint fields are preserved.

SQLite schema version 6 upgrades versions 2, 3, 4 and 5 in a transaction that contains
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
Its historical SQLite version 4-to-5 step adds only that projection without
replaying seeds; current startup also applies the version 6 ledger step.
Incremental DDL and the current marker commit together, and migration failure
leaves the prior marker and data.
See `tests/persistence/test_runner_presence_migrations.py` and
`tests/integration/test_runner_presence_migrations_postgres.py`.

Mission budget persistence adds accounts, operation-scoped reservations, and
final receipts without backfilling balances or replaying historical usage.
Accounts bind an exact Contract revision and a server pricing digest. Monetary
columns store nonnegative signed BIGINT nano-USD; the account limit cannot
exceed its Contract ceiling. Actual spending can exceed either ceiling so an
unexpected provider overrun remains visible. A separate `blocked` flag prevents
further admission after an overrun; lifecycle services own that decision.

Reservation CHECKs enforce dispatch identity, terminal cost, and an explicit
reason for UNKNOWN outcomes. Final receipts have unique provider receipt
identities and database triggers reject UPDATE and DELETE on both backends.
SQLite also rejects conflicting receipt INSERTs before replacement resolution,
so `INSERT OR REPLACE` cannot discard an existing reservation/provider receipt
even when `recursive_triggers` is off.
Server UTC creation/dispatch/finalization timestamps are stored as microsecond
ISO text with explicit state and ordering CHECKs; final receipt time and the
reservation's final time commit together.
The SQLite migration uses independent DDL that retains every ledger CHECK,
requires integer storage for money, and adds the missing legacy Contract
revision unique index needed by its composite foreign key. The generic legacy
translator is not used for these tables.

Budget storage supports native asyncpg PostgreSQL transactions and the existing
single-process SQLite writer. The known stateless `NeonHttpConnection` backend
is refused before budget DDL/version stamping and by the derived ledger
repository before ledger reads or writes. Legacy Neon HTTP transaction emulation
is not accepted as an atomic finance boundary.

The PostgreSQL d9-to-e0 startup migration and SQLite compatibility upgrades
commit ledger DDL with their version marker. A failed ledger migration rolls
back newly created tables, indexes, triggers, and the marker. Alembic downgrade
to d9 explicitly drops budget data and its receipt trigger function; operational
rollback must account for that data loss before choosing a schema downgrade.
Tests cover actual SQLite 2/3/4/5-to-6 upgrades and opt-in PostgreSQL head,
downgrade/reupgrade, constraints, and injected failure rollback:

```powershell
python -m pytest tests/persistence/test_mission_budget_migrations.py tests/integration/test_mission_budget_migrations_postgres.py -q
```
