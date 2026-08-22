# ADR-0096: Runner Heartbeat and Cancellation Supervision

> Status: implemented  
> Owner: execution maintainers  
> Date: 2026-08-22  
> Scope: `app/services/runner_service.py`, `services/python/runner_service/runtime.py`

## Context

Runner execution is authorized by a lease owned by Mission Control. A model or
tool call may run longer than one heartbeat interval, and a process may be
cancelled during execution or shutdown. Treating a completed local Harness as
success without lease supervision can produce work that no longer has authority
to publish Artifacts or complete a WorkUnit.

## Decision

The Runner runs the Harness and heartbeat supervisor as sibling tasks. The
first task to finish controls the outcome: a heartbeat error cancels the
Harness and fails the WorkUnit behind the original lease; caller cancellation
cancels the Harness, best-effort failure reporting is attempted, and the
cancellation is re-raised. A Harness failure, Artifact publication failure, or
control-plane reporting failure is converted to an explicit WorkUnit failure
when the lease still permits it. Heartbeat responses are checked against the
exact lease and attempt identity.

The service runtime separately supervises the workspace worker and closes its
owned network clients once during shutdown. A shutdown deadline cancels a stuck
worker; it does not create a local queue or mutate Mission state outside the
lease-fenced control API.

## Consequences

- Lease loss stops local execution before Artifact publication can claim success.
- Cancellation remains observable to callers while failure recovery remains
  durable when Mission Control is reachable.
- Heartbeat, Harness, MCP, Artifact, and A2A paths share the same explicit
  execution fence rather than creating parallel lifecycle state.

## Verification

- Runner, Harness, MCP, worker, and service-runtime tests pass together.
- The suite covers heartbeat failure, caller cancellation, failure-reporting
  failure, lease mismatch, artifact reporting failure, graceful shutdown, and
  shutdown deadline cancellation.
