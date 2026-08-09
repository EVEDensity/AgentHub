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
  the required context headers before dispatch.
- Output: normalized `MCPToolResult`, with text content joined for Harness
  feedback and MCP `isError` preserved.
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
capability scope but does not widen it. Audit events intentionally exclude
arguments, prompts, and tool result content. A future durable audit adapter
must add retention and ACL policy before production persistence. The Go
transport only attaches validated context to the request; it does not write
Mission state or treat headers as authorization proof by themselves.
