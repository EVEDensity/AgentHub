# Mission Control services

`MissionService` composes application commands over the Mission repository.
Mission, Contract, WorkUnit, leases, execution checkpoints, Artifact metadata,
Evidence, and Decisions remain the durable sources of truth. Mixins do not own
parallel state or execute model loops.

## Chat dispatch

`_chat_dispatch_mixin.py` admits one enabled `function-calling` Agent from the
workspace catalog and creates one `desktop.task` root for a started chat Mission.
The command locks the exact Mission, checks its workspace and source, rechecks
the catalog against the admitted participant snapshot, then persists the PENDING
WorkUnit and creation event in one transaction. Repeated admission returns the
same plan; it never rebinds existing work or creates a new attempt.

Catalog capability tags are descriptive. They are not copied into Contract tool
grants. The current chat Contract grants no tools and uses the shared default
`artifact-set.v1` output-integrity policy. This proves registered byte
availability, size and digest; it does not prove tests ran or code is correct.
Dispatch alone cannot create PASS Evidence or mark a Mission successful.

Workspace claims admit chat roots only for the exact `chat` / `desktop.task` /
`function-calling` tuple, assigned Agent, workspace, supported kind, and lease
owner. The desktop resolver preserves the chat source in bounded model context.
The built-in desktop process consumes only `local-desktop-agent`; another Agent's
unit remains PENDING until a Runner configured for that binding claims it.

Multi-executor chat workflows and outbound A2A chat routes are not implemented.
The adapter rejects those requests instead of redirecting them to the built-in
Agent. See ADR-0112 and `tests/api/test_chat_mission.py` for admission, SQLite,
transaction rollback, claim, and lease-fencing coverage.

## Trusted test admission

The explicit `trusted-test-run.v1` policy admits PASS only for the complete
registered current-attempt Artifact bundle. `_verify_mixin.py` checks that the
submitted five Artifacts match that complete immutable metadata set before byte
verification and again under the Mission/WorkUnit admission transaction locks.
Extra reports or another bundle cannot be omitted from a passing submission.
Existing evaluators retain their registered-subset behavior; FAIL Evidence uses
the existing admission path. See `trusted_tests/admission.py` and
`tests/integration/test_trusted_test_verification_sqlite.py`.

## Internal budget infrastructure

`MissionBudgetService` is an opt-in internal ledger, with no provider HTTP or
default Harness integration. It shares one Mission account across that Mission's
child WorkUnits and fixes the actual Contract revision and server price table.
Forked Missions have different account identities; cross-Mission inheritance is
pending. The exact ceiling is read from stored Contract JSON using Decimal, and
all charges/reservations use integer nano-USD. Per-million token prices are
server-owned; each complete call's fractional nano-USD rounds upward once.

Reserve and dispatch lock actual Mission/WorkUnit state and validate the current
Runner/attempt/lease on the same transaction connection as account and reservation
writes. Dispatch's CAS can be claimed once. A RESERVED operation can be released;
dispatched timeout/cancellation/missing usage becomes UNKNOWN and retains its
hold. Trusted late settlement changes financial rows only. An over-reservation
charge records the entire amount and blocks further calls. Final receipts are
immutable; UTC creation/dispatch/finalization times survive idempotent replays.

The ledger supports native asyncpg PostgreSQL transactions and SQLite's
single-process serialized writer. Neon HTTP SQL is explicitly refused: its
stateless BEGIN/COMMIT emulation cannot provide atomic ledger operations or row
locks. The budget migration refuses this backend before ledger DDL or stamping.

There is no Runner self-reported settlement endpoint. This internal accounting
layer does not make existing model calls budget-enforced. See
[ADR-0123](../../../docs/architecture/decisions/0123-persistent-mission-budget-ledger.md),
`tests/integration/test_mission_budget_sqlite.py` and the opt-in independent-
connection PostgreSQL tests in `test_mission_budget_postgres.py`.
