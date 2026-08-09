# Stateless MCP Adapter

> Status: implemented
> Owner: protocol and execution maintainers
> Last reviewed: 2026-08-09

## Responsibility

`StatelessMCPClient` adapts one HTTP JSON-RPC `tools/call` operation to the
Harness tool boundary. Each call is self-contained: it creates a request ID,
sends the complete Mission/WorkUnit/attempt/capability context, validates the
response ID and MCP result shape, and discards protocol state after the call.
`StatelessMCPToolAdapter` binds that client to one capability-granted
`FunctionTool` for one Harness execution. `build_mcp_capability_binding`
composes the client with `CapabilityToolResolver`; the resolver's Contract
scope is snapshotted into each call only after authorization.

## Inputs and outputs

- Input: `MCPToolCall` with `MCPCallContext`, tool name, and JSON-object
  arguments.
- Transport: an HTTP(S) endpoint receiving JSON-RPC `tools/call`; no
  `initialize` handshake, `sessionId`, or client-side MCP session store. The
  bundled Go Gateway exposes this contract at `POST /mcp/rpc` and validates
  the required context headers before dispatch. The HTTP route also requires
  a Bearer token verified by the shared IAM package.
- Output: normalized `MCPToolResult`, with text content joined for Harness
  feedback and MCP `isError` preserved.
- Tool authorization: the Go Registry assigns one static capability to every
  built-in tool and rejects a stateless call when the request capability does
  not exactly match that assignment. The tool handler is never entered on a
  mismatch.
- Tenant propagation: tenant-scoped Registry tools obtain tenant and actor
  only from the verified IAM context. Agent dispatch forwards the verified
  Bearer credential to the platform Gateway; document ingest records the actor
  in metadata. Session listing forwards the verified tenant and credential to
  the Gateway's `/platform/sessions` route; Gateway owns authentication and
  proxies the bounded query to Session Service. Missing identity or required
  downstream credential fails before any network request.
- Agent catalog projection: `list_agents` forwards the verified tenant and
  credential to Gateway `/platform/agent-registry`. Gateway derives the actor
  from IAM context, requires `agent:read`, reads the existing user-scoped Agent
  catalog as a read-only compatibility projection, and excludes provider
  credentials, base URLs, raw configuration, and avatar data from the MCP
  response.
- Audit: `MCPToolAuditEvent` containing request ID, correlation context, tool
  name, success, duration, and sanitized error type only.

## Failure behavior

Invalid endpoint, context, response shape, response ID, HTTP status, and JSON-
RPC errors fail at the adapter boundary. Tool-level `isError` results become
unsuccessful Harness tool feedback. Audit recording is fail-closed: if the
audit port cannot accept the event, the call fails rather than continuing with
an untracked result.

## Ownership and security

The adapter does not own MCP server sessions, tool registration, Mission state,
WorkUnit transitions, or durable audit storage. Capability authorization is
still supplied by `CapabilityToolResolver`; the binding forwards the resolved
capability scope but does not widen it. The server intersects that declaration
with the authenticated principal's `tool:execute` scope, optional
`required_scope`, and optional tenant constraint. Tenant and actor identity are
propagated in Go `context.Context` independently from the untrusted execution
headers. Audit events intentionally exclude arguments, prompts, and tool
result content. A future durable audit adapter must add retention and ACL
policy before production persistence. The Go transport only attaches validated
context to the request; it does not write Mission state or treat headers as
authorization proof by themselves.

The capability check is enforced on requests carrying validated stateless
execution context. The legacy SSE and STDIO transports remain compatibility
paths for clients that do not provide WorkUnit context; they must not be used
as the protected Harness execution boundary.

HTTP mode fails to start without `JWT_SECRET`. Local unsigned IAM dev mode
requires the explicit `MCP_ALLOW_INSECURE_DEV_AUTH=true` opt-in. The stateless
RPC route accepts credentials only from the Authorization header; the shared
IAM query-token compatibility path is not exposed there.
