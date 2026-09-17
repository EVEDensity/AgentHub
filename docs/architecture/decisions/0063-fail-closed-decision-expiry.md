# ADR-0063: Fail-Closed Decision Expiry

> Status: accepted  
> Owner: Mission Control maintainers  
> Date: 2026-08-16  
> Scope: Decision timeout policy, persistence, and lifecycle supervision

## Context

ADR-0062 introduced durable PENDING verification Decisions but left expiry
unset. A Mission could therefore remain in `WAITING_DECISION` forever when no
human responded. Reusing execution time or retry budgets would conflate model
work with human governance, while treating timeout as a human resolution or
cancellation would lose the reason the workflow closed.

Expiry must be durable, safe under multiple supervisors, unable to create PASS
Evidence, and atomic with the blocked WorkUnit and Mission transitions.

## Decision

Mission Control assigns each new verification Decision an `expiresAt` when it
is created. The current default is 24 hours and is injectable at composition
time. The chosen timestamp is stored on the Decision and is never recomputed
from later configuration. A future immutable Contract revision may supply an
explicit governance SLA without changing existing Decisions.

`EXPIRED` is a distinct terminal Decision status. It has no human resolution,
but records a service actor, rationale, closure time, and optimistic version
increment. Human resolution is rejected once `expiresAt` has passed, even when
the supervisor has not yet processed the row.

The stateless expiry command selects one oldest eligible Decision and its
`WAITING_DECISION` Mission with `FOR UPDATE ... SKIP LOCKED`. It then locks the
related WorkUnit and rechecks Decision status, expiry, Mission status, WorkUnit
status, and attempt. The transaction performs:

- Decision `PENDING -> EXPIRED`;
- WorkUnit `VERIFYING -> FAILED`;
- Mission `WAITING_DECISION -> FAILED`.

The command appends causally linked Decision, WorkUnit, and Mission events.
Timeout never retries automatically, consumes no retry budget, produces no
Evidence, and cannot grant PASS. A repeated command returns idle after the
first transaction commits.

The database adds explicit status and lifecycle constraints plus a partial
index over PENDING Decisions with non-null expiry. The runtime migration chain
and Alembic advance together to one head.

## Consequences

Unattended governance work no longer blocks a Mission indefinitely once a
supervisor invokes the command. Operators can distinguish timeout from human
resolution and Mission cancellation. Multiple process instances may compete
without processing the same Decision concurrently.

Failing the Mission is intentionally conservative. Automatic retry would make
silence equivalent to approval and could spend resources without a human
decision. Recovery requires an explicit future Contract-revision or Mission
recovery workflow.

This slice provides the durable command boundary but does not start a polling
loop inside the web process. A separately deployable supervisor composition,
metrics, and alerting remain required before claiming automatic production
expiry.

## Alternatives considered

- Leave Decisions pending forever: rejected because it creates permanent
  operational limbo.
- Reuse `CANCELLED`: rejected because operator cancellation and elapsed SLA are
  different audit facts.
- Resolve to `FAIL_MISSION`: rejected because expiry is not a human resolution.
- Retry automatically: rejected because timeout must fail closed.
- Use `budgets.timeSeconds`: rejected because execution and human wait budgets
  have different ownership and semantics.

## Verification

Domain and JSON Schema tests cover EXPIRED lifecycle requirements. Migration
tests cover status/lifecycle constraints, the partial index, runtime ordering,
and a single Alembic head. Repository tests cover deterministic multi-instance
candidate locking. Service tests cover the atomic transitions, causal events,
idempotent idle result, invalid time input, and post-deadline resolution denial.

## Extends

This decision extends [ADR-0062](0062-mission-owned-verification-decisions.md).
