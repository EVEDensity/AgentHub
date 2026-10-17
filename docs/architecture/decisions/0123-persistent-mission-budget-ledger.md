# ADR-0123: Persist an internal Mission budget ledger

> Status: accepted
> Owner: Mission Control maintainers
> Date: 2026-10-18
> Last reviewed: 2026-10-18
> Scope: M1b-a internal accounting infrastructure; model dispatch integration remains pending

## Context

ADR-0017 bounds request-local Harness usage but treats absent provider usage as
zero and does not persist Mission spending. That cannot support shared spending
reservations, crash uncertainty or financial reconciliation. A durable ledger
must precede any claim that money is enforced across real model requests.

## Decision

Add three additive tables owned by Mission Control: one account per Mission,
execution-scoped reservations and immutable final receipts. The account binds
the actual Mission Contract ID/version. Its ceiling comes from the stored
Contract JSON numeric text, parsed as Decimal; an optional internal operator
ceiling may only reduce it. Do not reinterpret the existing Python float budget
as exact money. Unsupported sub-nano precision and invalid or absent values fail
initialization rather than becoming zero.

USD is a non-negative signed 64-bit integer in nano-USD (10^9 units per USD).
Decimal/text/integer conversion is exact and rejects floats, booleans, negative,
nonfinite, sub-nano and overflowing inputs. Prices are a server-owned read-only
table. Each account fixes both its version and canonical table digest, and each
reservation stores its concrete input/output rates per million tokens. The sum
for a whole call rounds upward once to a nano-USD. Trusted usage supplies token
counts and provider receipt identity, never a claimed charge or price.

Reserve and dispatch require the actual RUNNING Mission and WorkUnit, current
attempt, Runner, lease ID and unexpired lease. All writes use the very same
connection/transaction as Mission repository operations. Lock order is Mission,
WorkUnit, account, reservation. Sub-WorkUnits in one Mission share this account.
A fork creates a new Mission and is not covered by this account; cross-Mission
budget inheritance is not implemented.

The operation key is `(mission_id, work_unit_id, attempt, operation_id)`.
Matching repeated payloads are idempotent; conflicting payloads are rejected.
Dispatch is an atomic RESERVED-to-DISPATCHED compare-and-set and returns whether
this caller claimed dispatch. The eventual provider integration must obey that
result and bind the digest to its exact request. This ledger does not send HTTP
or prove that a claim reached a provider.

Only an unsubmitted RESERVED operation may be released. Timeout, cancellation
or missing usage after dispatch produces UNKNOWN while retaining the complete
hold. UNKNOWN is a mutable reservation state, not a final receipt. Explicit
trusted zero usage is a final zero charge and is distinct from missing usage.
Trusted late usage may settle an old attempt after lease expiry or terminal
Mission/WorkUnit state, without changing either business state. Settlement uses
the reservation's frozen prices. A charge beyond the reservation is recorded
in full and blocks future reservations/dispatches, even when other Mission funds
remain. One provider receipt cannot be charged to two operations.

Final SETTLED/RELEASED receipts are INSERT-only, including database mutation
guards. Final receipt, reservation state and balances commit together. Server
UTC creation, dispatch and finalization times are persisted with state/order
checks. Retries return the original times; generated times are excluded from
the idempotency input digest. Arithmetic or persistence failure rolls back the
entire operation.

There is no public Runner settlement route. `MissionBudgetService` accepts
trusted in-process provider-accounting input. A typed usage value alone is not
proof of external authenticity: a future trusted provider integration must
establish that provenance. No existing ModelUsage, checkpoint v1, provider
adapter, Harness or default Chat/CLI/Desktop composition changes in this slice.

## Migration and verification

Alembic adds `e0b4c536f708` after `d9a3b425e6f7`. SQLite schema 6 upgrades
versions 2/3/4/5 with missing additive upgrades and the schema marker in one
transaction, without replaying seeds. New SQLite DDL keeps monetary, state and
time CHECKs rather than using the historical CHECK-stripping translator.

Real SQLite tests exercise shared-pool concurrency and rollback. SQLite's
guarantee is the existing single-process serialized writer. Opt-in PostgreSQL
tests use independent database connections to contend for funds and a single
dispatch, and cover actual migrations, rollback and downgrade/re-upgrade.
Only native asyncpg PostgreSQL and that SQLite writer are supported. The known
stateless Neon HTTP SQL backend is refused before ledger repository access or
budget migration DDL/stamping: HTTP BEGIN/COMMIT emulation does not establish a
real shared transaction or row locks. This decision does not repair the legacy
Neon adapter's wider transaction behavior.
Absent `AGENTHUB_TEST_POSTGRES_DSN` explicitly skips those tests; only execution
against PostgreSQL establishes that backend evidence.

Implementation and executable evidence:
[internal service](../../../app/services/mission_budget_service.py),
[SQLite accounting flow](../../../tests/integration/test_mission_budget_sqlite.py),
[PostgreSQL accounting flow](../../../tests/integration/test_mission_budget_postgres.py),
and [migration checks](../../../tests/integration/test_mission_budget_migrations_postgres.py).

This infrastructure does not establish an effective hard model budget. That
requires a later strict call profile covering every real provider HTTP request,
bounded request/output parameters, transport retry/cancellation uncertainty,
trusted usage reconciliation and operation identity across restart. Cross-Mission
fork authority and mission-wide token limits remain separate work.

## Supersedes

Partially supersedes ADR-0017 only for this new experimental persistent ledger:
missing usage is UNKNOWN rather than zero, and money is durable Mission-owned
state. Existing Harness behavior remains governed by ADR-0017 until explicitly
integrated and migrated.
