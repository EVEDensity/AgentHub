# ADR-0021: MCP Stateless HTTP Context Validation

> Status: accepted
> Owner: protocol and execution maintainers
> Date: 2026-08-09
> Scope: Go MCP Gateway stateless transport

## Context

ADR-0019 introduced a stateless client port, but the Go Gateway only exposed
an SSE transport with a process-local session map. Without a server-side
stateless endpoint, the client contract could not be exercised against the
gateway and request context could be silently dropped at the protocol edge.

## Decision

Add `POST /mcp/rpc` backed by `StatelessHTTPTransport`. Every request must
include non-empty Mission ID, WorkUnit ID, capability, a positive attempt, and
a JSON-object capability scope in `X-AgentHub-*` headers. An optional trace ID
is propagated. The transport bounds the JSON body, rejects non-POST, malformed
JSON, missing/invalid context, and response batches, and attaches a validated
`MCPRequestContext` to the `context.Context` passed to the existing protocol
dispatcher.

The transport has no session map, initialize handshake, or lifecycle writes.
The existing SSE `/mcp/sse` and `/mcp/message` routes remain compatibility
transport and are not silently reinterpreted as stateless. Context headers are
request metadata, not authentication or capability authorization; upstream IAM
and the Contract resolver remain responsible for those decisions.

## Consequences

The Python Stateless MCP client can call the Gateway directly without a
connection/session dependency, and registry handlers can consume correlation
context through the standard Go context. Stateless requests are independently
routable and restart-safe at the transport layer. Durable audit, context ACLs,
and Mission/WorkUnit authorization remain outside the transport.

## Alternatives considered

- Reuse SSE `sessionId` for the new client: rejected because it preserves
  process-local protocol state and fails restart-safe routing.
- Store request context in a Gateway global map: rejected because it would
  recreate a session state source and introduce cleanup races.
- Have the transport update WorkUnit state: rejected because Mission Control
  owns durable lifecycle transitions.

## Verification

- Go transport tests cover context propagation, required-header validation,
  body limits, method enforcement, and notification responses.
- Python client and capability-binding tests exercise the same header contract.
- Existing Go and Python service regressions remain green.

## Supersedes

None. This implements the server edge described as follow-up work in ADR-0019.
