# ADR-0012: Runner Heartbeat and Cancellation Supervision

> Status: accepted
> Owner: architecture maintainers
> Date: 2026-08-08
> Scope: Runner execution lifecycle and local Sandbox cancellation

## Context

ADR-0011 established the minimum Runner execution loop, but an execution that
outlived its original lease could continue without proving it still owned the
WorkUnit. Caller cancellation could also leave a local subprocess running after
the control-plane attempt was no longer active.

Mission Control already owns lease renewal and validates the WorkUnit's lease
ID, attempt, identity, and expiry transactionally. The Runner needs to use that
control-plane command while retaining no durable lifecycle state of its own.

## Decision

The Runner supervises every started Sandbox execution with a concurrent lease
heartbeat loop. Unless configured explicitly, it renews at one-third of the
lease duration, bounded between 0.1 and 30 seconds. Every renewal response
must match the lease ID and attempt acquired at the start of the run.

If a heartbeat request fails, is rejected, or returns a mismatched lease
context, the Runner cancels the local execution, waits for its cancellation,
records an explicit WorkUnit failure through Mission Control, and never
publishes or completes an Artifact for that attempt. If the caller cancels the
Runner, it follows the same local cancellation and best-effort failure-recording
path before propagating cancellation to the caller.

The local subprocess Sandbox kills and reaps its child process when its
execution coroutine is cancelled. The current remote Sandbox HTTP API has no
remote cancellation command, so cancellation of its client request cannot claim
to terminate an already-started remote container. That limitation remains
observable as a future protocol requirement rather than being hidden as a full
distributed cancellation guarantee.

## Consequences

Long-running local executions retain their lease only while the Runner can
continue proving ownership to Mission Control. A stale, partitioned, or
superseded Runner stops reporting success and cannot turn old output into a
new Artifact completion. The Runner remains stateless and interacts with
durable state only through the versioned Mission Control API.

This introduces one supervision task per active execution and requires the
control-plane heartbeat endpoint to stay available through the lease interval.
Remote Sandbox cancellation needs a later, separately versioned operation
before this guarantee applies beyond the Runner process.

## Alternatives considered

- Renew only before reporting completion: rejected because a long execution
  could run after its lease expired.
- Let the Sandbox renew leases directly: rejected because it would give an
  execution adapter control-plane lifecycle ownership.
- Ignore caller cancellation until a command timeout: rejected because it
  wastes local resources and risks unowned execution.
- Claim HTTP request cancellation stops remote work: rejected because the
  current remote Sandbox API exposes no such command.

## Verification

- Runner tests cover a heartbeat before Artifact reporting, heartbeat failure
  cancelling execution, lease-fenced heartbeat requests, and caller
  cancellation with an explicit failure transition.
- Sandbox tests verify that cancelling a local subprocess prevents its delayed
  completion side effect.
- Existing Mission API and persistence tests continue to verify the durable
  `LEASED -> RUNNING -> VERIFYING` state path and lease fencing.

## Supersedes

None. This hardens the execution lifecycle introduced by ADR-0011.
