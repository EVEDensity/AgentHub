# ADR-0036: Restore MCP `call_agent` as a Controlled Delegation Command

> Status: accepted  
> Owner: protocol and Mission Control maintainers  
> Date: 2026-08-15  
> Scope: MCP Gateway, Mission delegation boundary, Agent execution

## Context

The legacy `call_agent` behavior was a chat-oriented invocation path. It could
publish a session message, use fallback Agents, and return text without creating
a durable WorkUnit or producing Artifact/Evidence provenance. Re-enabling that
behavior through MCP would create a second execution truth and bypass leases,
capability bindings, and failure recovery.

The delegated WorkUnit path now has the required controls: parent lease fencing,
scope-aware Agent binding, Runner claim, Artifact ownership, byte integrity, and
Evidence admission.

## Decision

The Go MCP Registry advertises `call_agent` only under the exact
`agent.delegate` capability. The stateless request must include Mission,
parent WorkUnit, attempt, capability, and scope context, plus an authenticated
IAM credential. Its arguments must explicitly provide:

- a child WorkUnit id and registered Agent id;
- the active parent lease id;
- at least one existing `ArtifactRef` input;
- output specifications; and
- the child capability requirements.

The Gateway forwards these fields to
`POST /api/v1/missions/{mission}/work-units/{parent}/delegations` and returns
the JSON response only when Mission Control accepts the command. It does not
execute model/tool loops, resolve fallback Agents, accept free-form synthetic
tasks, or claim success. Mission Control remains responsible for validating the
parent lease, Contract, Agent binding, and Artifact provenance; Scheduler and
Runner remain responsible for claim, execution, recovery, and evidence.

## Consequences

MCP clients can initiate real multi-agent delegation without restoring the
legacy chat execution path. A successful tool response means only that a child
WorkUnit was durably accepted; clients must observe its lifecycle and artifacts
separately. The explicit ArtifactRef requirement makes delegation unsuitable
for untracked prompt-only handoffs, which is intentional until a versioned
input-artifact creation command exists.

## Alternatives considered

- Reconnecting MCP to `agent_service.call_agent` was rejected because it runs a
  model loop outside Mission Control and has no lease/evidence fence.
- Preserving fallback Agent chains was rejected because binding resolution must
  be deterministic and tenant/workspace scoped.
- Returning `success=true` with a text acknowledgement was rejected because it
  would be indistinguishable from completed execution to callers.

## Verification

- Registry tests assert the capability binding, required delegation fields,
  authenticated credential forwarding, Mission API path, and missing-context
  fail-closed behavior.
- Existing Python Mission, Runner, Artifact, Evidence, and stateless MCP tests
  remain green.

## Supersedes

None.
