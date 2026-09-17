# ADR-0013: Harness and Runner Boundary

> Status: accepted
> Owner: architecture maintainers
> Date: 2026-08-08
> Scope: execution ownership between Harness and Runner

## Context

The Runner vertical slice now has lease fencing, heartbeats, cancellation, and
Artifact publication, but execution is still coupled directly to a Sandbox
port. The target architecture requires a Harness that can later own model
calls, function calling, tool use, bounded loops, checkpoints, and usage
accounting without creating another durable WorkUnit state machine.

## Decision

Introduce a request-scoped `HarnessPort` with `HarnessRequest` and
`HarnessResult`. Runner owns lease acquisition, controlled start, heartbeat and
cancellation supervision, isolated execution boundary, Artifact publication,
and Mission Control reporting. Harness owns execution-loop concerns and returns
an honest `SandboxResult` plus loop metadata.

The initial `SandboxHarness` implementation delegates exactly one bounded
request to the existing Sandbox port. It is an intentional compatibility slice:
it creates a stable seam for future model/tool loops while preserving the real
subprocess/remote Sandbox behavior already tested. It does not claim model
calling, tools, retries, or checkpoints until those capabilities have their own
contracts and tests.

## Consequences

Runner can be tested with a Harness double and no longer needs to know how an
execution loop is implemented. Existing callers that inject `SandboxPort`
remain compatible through the default `SandboxHarness`. Future Harness
capabilities can evolve independently while Mission Control remains the only
durable lifecycle owner.

## Alternatives considered

- Move the existing large AgentService loop into Runner: rejected because it
  would couple transport-era state and legacy task concepts to WorkUnit state.
- Add a Harness interface without a working implementation: rejected because
  the product path must remain executable, not a placeholder.
- Let Harness report WorkUnit status directly: rejected because it creates a
  second source of business truth.

## Verification

- Harness unit tests verify request forwarding, timeout validation, and loop
  metadata defaults.
- Runner tests verify explicit Harness delegation and preserve the existing
  lease, heartbeat, cancellation, Artifact, and Mission API regressions.

## Supersedes

None. This refines the execution ownership introduced by ADR-0011 and ADR-0012.
