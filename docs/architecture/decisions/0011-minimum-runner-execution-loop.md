# ADR-0011: Minimum Runner Execution Loop

> Status: accepted  
> Owner: architecture maintainers  
> Date: 2026-08-08  
> Scope: Runner orchestration, Mission Control command adapter, and execution failure handling

## Context

Mission Control already exposes lease, controlled start, Artifact registration,
completion, failure, and recovery commands. Without a Runner caller, those
transitions could only be exercised manually and the product still lacked a
real execution-to-Artifact path. The Runner must remain replaceable and must
not create a second durable WorkUnit state machine.

## Decision

Add a small Runner orchestration module and an HTTP Mission Control adapter.
For one bounded command, the Runner:

1. obtains a lease and validates the returned lease ID and attempt;
2. starts the WorkUnit through the controlled-start command;
3. executes through the isolated `SandboxPort`;
4. records an honest failure on execution failure;
5. publishes stdout through the Runner-owned Artifact publisher;
6. registers the returned immutable digest metadata; and
7. completes the WorkUnit with the matching `ArtifactRef`.

The adapter forwards the existing versioned HTTP routes with Runner
authorization. It does not access the Mission repository directly. Reporting,
storage, or control-plane failures are raised rather than converted into
success; after start, the Runner makes a best-effort failure transition before
surfacing the error.

This first loop requires the execution timeout to fit within the requested
lease. Lease heartbeats and cancellation-aware execution supervision are the
next hardening slice; expired leases are still fenced by Mission Control and
recoverable through its existing command.

## Consequences

The repository now has a real, testable path from isolated command output to
registered Artifact bytes and WorkUnit `VERIFYING`, including the existing event
ledger. The Runner remains stateless between calls and can later move to an
independent service without changing Mission contracts. A long-running command
must wait for the heartbeat hardening before production use.

## Alternatives considered

- Let the API handler execute commands: rejected because transport would own
  isolation, lease timing, and output publication.
- Update WorkUnit rows directly from Runner code: rejected because Mission
  Control must remain the sole durable state owner.
- Mark successful execution as `SUCCEEDED`: rejected because independent
  Evidence remains a separate verifier responsibility.
- Build a fleet scheduler now: deferred until this single loop is observable
  and restart/recovery behavior is proven.

## Verification

- Unit tests cover ordered lease/start/reporting calls, execution failures,
  lease mismatch, control rejection, and best-effort failure recording.
- A real subprocess plus local publisher test verifies actual bytes.
- An ASGI integration test drives the existing Mission API and asserts the
  `LEASED → RUNNING → VERIFYING` events and Artifact projection.

## Supersedes

None. This decision implements the Runner execution portion of the target
architecture and depends on ADR-0009 and ADR-0010.
