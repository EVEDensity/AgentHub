# ADR-0028: MCP Agent Calls Require Durable Delegation

> Status: accepted
> Owner: protocol and control-plane maintainers
> Date: 2026-08-09
> Scope: MCP `call_agent` registration and Agent delegation semantics

## Context

The MCP Registry advertised `call_agent` as sending work to a selected Agent
and returning its response. The handler instead posted the message to Gateway
`/publish`. That endpoint emits a `session.message.received` event and returns
a publish receipt. It does not create a WorkUnit, resolve an execution adapter,
acquire a lease, run an Agent, register an Artifact, or collect Evidence.

Returning that receipt as tool content therefore represented message delivery
as Agent execution. It also generated session and trace identifiers inside the
adapter, creating protocol state unrelated to the current Mission and WorkUnit.

The current WorkUnit contract accepts immutable ArtifactRefs as inputs. MCP
execution context carries Mission, WorkUnit, attempt, capability, and scope,
but not the active lease needed to register a new input Artifact. Creating a
child WorkUnit from the raw message would consequently create an unexecutable
unit with no durable input. Publishing directly to Agent Runtime would bypass
the lease-fenced Runner path.

## Decision

Remove `call_agent` from the built-in MCP tool registry until a durable Agent
delegation command exists. `tools/list` must not advertise it, and a direct
`tools/call` request receives the normal explicit tool-not-found error without
calling Gateway or publishing an event.

The `agent.dispatch` capability remains a valid Contract capability. It is not
mapped to session-message publication.

Restoring an MCP Agent delegation tool requires all of the following:

- the requested input is persisted as an immutable Artifact and referenced by
  the delegated WorkUnit rather than copied into an event payload;
- Mission Control atomically creates a causally linked delegated WorkUnit under
  the authorized Mission and validates its Contract capability;
- target Agent resolution is tenant-scoped and produces an explicit adapter
  binding without exposing provider credentials;
- execution starts only through scheduler, lease, Runner, and Harness control;
- the API distinguishes accepted asynchronous delegation from a verified Agent
  result and never returns a dispatch receipt as successful execution;
- results use registered Artifacts and independent Evidence before terminal
  success.

## Consequences

Models no longer see or invoke a tool that can only produce synthetic success.
Existing callers receive an honest unsupported result and can use explicit A2A
operations for configured external Agents. Internal Agent delegation remains a
planned control-plane capability, not an alternate session or AgentNet state
machine.

This temporarily removes an MCP feature from discovery. That is preferable to
an unreliable capability that cannot be resumed, verified, or reconciled after
process failure.

## Alternatives considered

- Keep `/publish` and improve HTTP error handling: rejected because a reliable
  message receipt is still not an Agent result.
- Create an empty child WorkUnit and publish the raw prompt separately: rejected
  because the durable unit cannot reconstruct its input after restart.
- Publish directly to Agent Runtime: rejected because it bypasses lease fencing
  and Mission Control state transitions.
- Route every call through A2A: rejected because configured local model Agents
  are not necessarily external A2A endpoints; A2A remains an edge adapter.

## Verification

- Registry tests assert `call_agent` is absent from `tools/list`.
- Direct invocation returns tool-not-found and performs no network request.
- Existing MCP authentication, capability, Resource, and adapter contract tests
  remain green.

## Supersedes

The `call_agent` forwarding decision in ADR-0024. Document-ingest tenant and
actor propagation from ADR-0024 remains accepted.
