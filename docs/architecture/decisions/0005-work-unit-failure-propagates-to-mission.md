# ADR-0005: WorkUnit Failure Propagates to Mission

> Status: accepted  
> Owner: architecture maintainers  
> Date: 2026-08-08  
> Scope: Mission Control terminal failure consistency

## Context

A WorkUnit can become terminally `FAILED` through execution failure,
independent verification failure, pre-execution adapter failure, or an expired
lease after its retry budget is exhausted. Leaving its Mission in `RUNNING` or
`VERIFYING` creates a durable state that cannot make progress because terminal
WorkUnits cannot be retried.

## Decision

Mission Control propagates a terminal WorkUnit failure to its active Mission.

- WorkUnit and Mission snapshots and events are updated in one transaction.
- The WorkUnit failure or lease-expiry event is appended before the
  `mission.lifecycle.failed` event.
- The Mission event records the WorkUnit ID and failure reason, and its
  `causationId` references the WorkUnit event.
- Both `RUNNING` and `VERIFYING` Missions may become `FAILED` through this path.
- Protocol adapters consume the resulting Mission state and must not append a
  second failure transition.

## Consequences

Mission state no longer remains active after an unrecoverable unit of work has
failed. Event consumers can trace the terminal Mission decision to the exact
WorkUnit event. Recoverable failures must enter `RETRYING` before becoming
terminal; this rule does not remove or bypass the Contract retry budget.

## Alternatives considered

- Keep the Mission active and require a separate failure command: rejected
  because no valid WorkUnit transition remains to restore progress.
- Let adapters propagate failure: rejected because adapters are not owners of
  Mission business state and different protocols would diverge.
- Infer Mission failure only in read projections: rejected because it leaves
  the durable Mission snapshot and event history inconsistent.

## Verification

- API tests cover runner failure, verifier failure, and exhausted lease
  recovery.
- A2A regression tests ensure adapter dispatch failure still emits exactly one
  WorkUnit failure and one Mission failure.
- Mission failure events retain a causal link to the triggering WorkUnit event.

## Supersedes

None.
