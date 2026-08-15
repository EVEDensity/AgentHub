# ADR-0062: Mission-Owned Verification Decisions

> Status: accepted  
> Owner: Mission Control and verification maintainers  
> Date: 2026-08-16  
> Scope: Decision persistence, verifier discovery, and Mission lifecycle

## Context

Discovery version 3 attributes an inconclusive verification policy to exact
Contract criteria, but previously returned the same WorkUnit on every poll.
The verifier could neither resolve the policy nor safely choose one criterion,
and Mission Control stored no durable request for human action. Continuing to
record INCONCLUSIVE Evidence would create a second representation of unresolved
workflow state without stopping rediscovery.

The system already reserves `MissionStatus.WAITING_DECISION`; WorkUnit has no
equivalent because a Decision is a Mission governance boundary rather than an
execution queue state. A valid solution must be transactional, idempotent,
human-controlled, and unable to manufacture PASS Evidence.

## Decision

Mission Control owns a versioned Decision aggregate and PostgreSQL projection.
A verification Decision binds one Mission, WorkUnit, positive attempt,
canonical context digest, policy reason, sorted criterion IDs, offered options,
recommendation, and risk summary. It records request and closure actors and
timestamps. `(work_unit_id, attempt, context_digest)` is unique.

The context digest is canonical SHA-256 over Mission, Contract version,
WorkUnit kind and attempt, inconclusive reason and criteria, and sorted Artifact
ID/digest/size observations. It is an idempotency and drift-detection key, not a
signature or external attestation.

When authorized verifier discovery resolves an inconclusive policy, Mission
Control creates one PENDING Decision and transitions the Mission from RUNNING
or VERIFYING to `WAITING_DECISION` in the same transaction. The WorkUnit remains
VERIFYING, its Artifact set remains immutable, and the response still exposes
the v3 inconclusive context to the initiating verifier. Subsequent discovery
does not select the waiting Mission. Discovery creates no verifier lease or
claim, but it is no longer side-effect free for inconclusive policy.

The `mission:verify` principal only triggers this deterministic server-owned
transition. It cannot choose options or resolve the Decision. Resolution is a
workspace-authorized human command with `expectedVersion`; it locks Decision,
Mission, and WorkUnit and rechecks status, attempt, option, and retry budget.

The minimum options are:

- `RETRY_WORK_UNIT`: available only while Contract retry budget remains;
  transitions VERIFYING to RETRYING and WAITING_DECISION to RUNNING.
- `FAIL_MISSION`: transitions the blocked WorkUnit and Mission to FAILED.

No option grants PASS, accepts Artifact content, or bypasses Evidence. Direct
INCONCLUSIVE Evidence admission is rejected; historical rows remain readable.
Cancelling a waiting Mission atomically marks every PENDING Decision CANCELLED
and cancels nonterminal WorkUnits, preventing orphaned governance state.

Decision request, resolution, cancellation, WorkUnit transition, and Mission
transition each append causally linked events. Decision updates increment its
version. The v1 Decision schema is a stable cross-process projection.

The database revision also restores the previously omitted Alembic wrappers for
delegation, Agent binding, catalog projection, and inbound A2A source mapping.
Those wrappers reuse existing runtime-tested SQL and make the declared revision
chain executable before the new Decision head.

## Consequences

Inconclusive policy becomes visible human work rather than verifier retry noise.
Mission Control remains the only lifecycle authority, and a stronger future
model or verifier cannot silently override governance. Retry produces a new
WorkUnit attempt; if policy remains inconclusive, a new attempt-bound Decision
may be requested explicitly rather than reopening an old context.

Verifier readiness may be false for the poll that receives the inconclusive
context and become ready on the following idle poll. Operators must treat the
Mission Decision inbox, not repeated verifier errors, as the actionable queue.

This slice does not revise immutable Contracts, expire Decisions automatically,
or provide a frontend Decision inbox. Those require separate policy, scheduler,
and product work.

## Alternatives considered

- Persist INCONCLUSIVE Evidence only: rejected because Evidence is an
  observation, not a durable human-action state, and polling would continue.
- Add `WAITING_DECISION` to WorkUnit: rejected because the Mission is blocked by
  governance while the WorkUnit remains a completed attempt awaiting outcome.
- Let verifier submit a Decision or choose PASS: rejected because executing or
  verifying services cannot own human authority or self-approve success.
- Retry the same attempt after resolution: rejected because an unchanged
  context could reopen indefinitely and would weaken attempt idempotency.
- Create Decision asynchronously after discovery: rejected because crashes
  could strand a Mission between observed policy and durable state.

## Verification

Domain and JSON Schema tests cover PENDING, RESOLVED, and CANCELLED invariants.
Repository and migration tests cover round trips, row locks, uniqueness SQL,
runtime upgrade ordering, and a single Alembic head. API tests drive discovery
into WAITING_DECISION, prove repeated discovery is idle, list and resolve with
version fencing, enforce retry budget, reject verifier resolution, reject
Evidence before Artifact I/O while waiting, and close pending Decisions on
Mission cancellation.

## Supersedes

This decision implements the lifecycle deferred by
[ADR-0061](0061-inconclusive-policy-criterion-attribution.md) and narrows the
side-effect-free discovery statement in
[ADR-0055](0055-verifier-work-discovery.md). Verifier independence from
[ADR-0059](0059-independent-verifier-coordinator.md) remains unchanged.
