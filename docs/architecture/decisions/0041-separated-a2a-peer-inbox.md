# ADR-0041: Separate A2A Peer Inbox from Outbound Dispatch

> Status: accepted  
> Owner: protocol, security, Mission Control, and deployment maintainers  
> Date: 2026-08-15  
> Scope: A2A task direction, peer authentication, public Card routing, and
> inbound Mission translation

## Context

The Gateway previously published a task endpoint that also accepted local
outbound routing fields. When two AgentHub instances called each other, the
forwarded `agentUrl` could cause the receiver to delegate the same request
again. The remote call also had no receiver-issued credential boundary, while
reusing the local user's Authorization would violate origin and authority
ownership.

Agent Card trust answers which peer identity is accepted. It does not authorize
that peer to call a protected receiver endpoint, nor does it distinguish an
outbound delegation Mission from work received for local execution.

## Decision

Gateway separates the two protocol directions:

- `/platform/a2a/tasks` is the authenticated local command endpoint for
  outbound submit, get, and cancel.
- `/platform/a2a/inbox` is the authenticated peer endpoint advertised in the
  AgentHub Card and accepts only inbound send and cancel.
- `/.well-known/agent-card.json` is mounted at the standard unauthenticated root
  path. The peer inbox remains behind Gateway IAM.
- Outbound forwarding removes `agentUrl` and `target`, overwrites
  `sourceAgentUrl` with the sender's own Card URL, and restricts redirects to
  the configured peer origin.
- A peer Card advertising Bearer auth requires a receiver-issued token from a
  read-only file configured by exact origin in
  `A2A_PEER_BEARER_TOKEN_FILES_JSON`. The caller's token is never forwarded and
  there is no fallback credential.
- Inbound send re-probes and verifies the source Card, validates requested
  capabilities, and asks Mission Control to create a directionally isolated
  `a2a.inbound` Mission source and WorkUnit. Mission Control first selects an
  enabled, capability-complete local Agent from the credential-free workspace
  catalog. The binding must include `a2a.receive`; its Agent ID and local adapter
  type become the WorkUnit execution snapshot. It never assigns the outbound
  A2A adapter. The durable external identity is `(workspace, source origin, task
  ID)`; inbound cancel requires all three values and only resolves that mapping.
- Peer credentials, pins, and signing material remain startup protocol
  configuration and never become Registry or Mission data.

Mission Control remains the only durable lifecycle authority. A remote JSON-RPC
success is only an acceptance projection; a remote protocol or authentication
failure is written back to the sender Mission as `FAILED`.

## Consequences

Two AgentHub instances can interoperate without recursive forwarding or
credential delegation. Operators must provision both identity pins and the
receiver-issued authentication token. Token and pin rotation currently require
a Gateway restart.

The inbound WorkUnit is now durable and has a controlled local Agent binding.
Catalog failure or lack of a complete match rejects admission with no new
Mission state. Existing WorkUnits retain their binding snapshot across retries.
Root inbound claim support, trusted Runner input resolution, and the resulting
Artifact/Evidence path remain a separate release gate; the Gateway does not
fabricate execution. General third-party A2A conformance and remote
status/artifact synchronization also remain separate release gates.

## Alternatives considered

- Keeping one `/tasks` endpoint and using a boolean direction flag was rejected
  because routing fields would still cross the peer boundary and direction
  would remain caller-controlled.
- Forwarding the user's Authorization was rejected because the receiver, not
  the sender's user session, owns peer authorization.
- Treating a trusted Card signature as endpoint authorization was rejected
  because public identity proof does not grant workspace access.
- Creating inbound work as `a2a.delegate` was rejected because Runner would
  dispatch it back out and recreate the recursion.

## Verification

Gateway tests run two signed handlers with reciprocal strict origin pins and
receiver-issued bearer tokens. They verify Card capability probing, durable
sender submit, isolated receiver accept, absence of recursive outbound submit,
remote cancel, and sender failure write-back. Python API, migration, and
contract tests verify deterministic inbound identity, directional uniqueness,
controlled cancellation, workspace/capability-scoped catalog binding, binding
snapshot idempotency, fail-closed admission, and `a2a.inbound` schema support.

## Supersedes

None. This refines ADR-0036 through ADR-0040 without changing their trust and
Mission ownership decisions.
