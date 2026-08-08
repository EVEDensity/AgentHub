# ADR-0006: Mission Cancellation Cascades to WorkUnits

> Status: accepted  
> Owner: architecture maintainers  
> Date: 2026-08-08  
> Scope: Mission Control cancellation consistency

## Context

Cancelling a Mission previously changed only the Mission snapshot. Pending and
executing WorkUnits could retain active states and leases even though all
runner commands were fenced by the cancelled Mission. This left contradictory
durable state and made operational recovery ambiguous.

## Decision

Mission Control owns cancellation of the full Mission aggregate boundary.

- The Mission and all of its WorkUnits are locked and updated in one
  transaction.
- The Mission cancellation event is recorded first.
- Every non-terminal WorkUnit transitions to `CANCELLED`; active leases are
  released by the domain transition.
- Each WorkUnit cancellation event references the Mission cancellation event
  through `causationId`.
- `SUCCEEDED`, `FAILED`, and already `CANCELLED` WorkUnits remain unchanged.
- Protocol adapters call the Mission cancellation command and consume its
  resulting WorkUnit projection instead of maintaining a second cancellation
  path.

## Consequences

Cancellation produces one coherent durable snapshot after restart. Runners can
no longer observe cancelled Missions with apparently active WorkUnits, and
event consumers can trace every child cancellation to the user-level Mission
command. Cancelling a large Mission locks its WorkUnit set for the duration of
the transaction, which is preferred over partially cancelled state.

## Alternatives considered

- Leave WorkUnits active and infer cancellation in projections: rejected
  because durable state and leases remain misleading.
- Let each adapter cancel its mapped WorkUnit: rejected because protocol
  adapters do not own Mission lifecycle semantics.
- Cancel WorkUnits asynchronously after committing the Mission: rejected
  because crashes can leave a partially cancelled aggregate.

## Verification

- Mission API tests cover mixed pending, running, and successful WorkUnits.
- Persistence tests verify the full Mission WorkUnit set is locked for update.
- A2A cancellation regression tests continue to use Mission as business truth.

## Supersedes

None.
