# ADR-0019: Stateless MCP Tool Adapter

> Status: accepted
> Owner: architecture maintainers
> Date: 2026-08-09
> Scope: Harness-to-MCP tool calls and adapter-boundary audit

## Context

The Go MCP Gateway currently exposes an SSE transport with process-local
connection sessions. Reusing that session map inside Harness would make model
tool calls dependent on connection lifetime and would blur the boundary
between protocol state and Mission execution state. Harness needs a small
client port that can call a remote MCP tool while preserving the Contract and
WorkUnit capability context on every request.

## Decision

Add a Python `MCPClientPort` and `StatelessMCPClient` for one HTTP JSON-RPC
`tools/call` operation. Each call creates an independent numeric request ID,
sends the tool name and JSON-object arguments, and includes these headers:

- `X-AgentHub-Mission-Id`
- `X-AgentHub-Work-Unit-Id`
- `X-AgentHub-Attempt`
- `X-AgentHub-Capability`
- `X-AgentHub-Capability-Scope`
- optional trace and authorization headers

The client does not initialize an MCP session, keep a session ID, cache tool
results, or own retries. It validates HTTP, JSON-RPC ID, error, and MCP content
shapes and returns a request-local `MCPToolResult`. `StatelessMCPToolAdapter`
binds the client to a Harness `FunctionTool`; MCP `isError` results become
unsuccessful Harness feedback.

Every call can be sent to an `MCPAuditPort`. `MCPToolAuditEvent` contains only
request ID, Mission/WorkUnit/attempt/capability context, tool name, outcome,
duration, and sanitized error type. Audit failure is fail-closed. The adapter
does not persist audit records itself; future durable storage belongs behind
this port.

## Consequences

MCP tools can participate in the existing Contract-scoped Harness loop without
introducing MCP server sessions into Runner or Mission Control. The current
adapter targets a stateless HTTP JSON-RPC endpoint; the existing SSE gateway
session transport remains a separate compatibility surface until its business
semantics are migrated. Capability authorization still comes from
`CapabilityToolResolver`; forwarding a scope is not an authorization grant.

Network retries, tool discovery synchronization, streaming content, durable
audit retention, and server-side request-context enforcement remain explicit
follow-up work.

## Alternatives considered

- Store an MCP session in Harness: rejected because restart and concurrency
  would couple execution to process-local protocol state.
- Let MCP server responses update WorkUnit or Mission state: rejected because
  Mission Control is the only durable lifecycle owner.
- Record full arguments and tool content in audit events: rejected because
  redaction, retention, and ACL policy are not yet part of this boundary.

## Verification

- Adapter tests cover repeated context-bearing calls with no session ID,
  response normalization, HTTP/protocol failure auditing, Harness error
  feedback, audit fail-closed behavior, and configuration validation.
- Harness, Runner, Artifact, Mission API, persistence, migration, and A2A
  regression suites remain green.

## Supersedes

None. This extends the MCP boundary in the accepted system architecture and
the Harness contracts from ADR-0014 through ADR-0018.
