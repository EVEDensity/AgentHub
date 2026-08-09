# ADR-0023: MCP Server Tool Capability Binding

> Status: accepted
> Owner: protocol and execution maintainers
> Date: 2026-08-09
> Scope: Go MCP Registry and stateless `tools/call`

## Context

ADR-0020 makes the Python Harness resolver the source of Contract grants, and
ADR-0022 authenticates the caller at the Go MCP Gateway. The server still
accepted a caller-supplied capability header while dispatching any registered
tool. A caller could therefore present a valid `tool:execute` identity and a
different capability declaration to reach a tool outside the intended
Contract binding.

## Decision

Every built-in `RegisteredTool` has exactly one non-empty static capability
binding owned by the Go Registry. Registration fails closed for missing names,
capabilities, handlers, or duplicate names. Before executing a tool, the
Registry compares the validated stateless `MCPRequestContext.Capability` with
the registered capability. A mismatch returns an error and the tool handler is
not called.

The capability is an exact identifier, not a prefix or wildcard. Scope policy
continues to come from the request's Contract declaration and the IAM
intersection in ADR-0022; this decision only binds the requested tool name to
the capability that is allowed to expose it.

Calls without a stateless execution context remain accepted for the existing
SSE/STDIO compatibility transports. Those transports are not the protected
Harness execution boundary and are slated for separate deprecation or context
adaptation.

## Consequences

Direct stateless callers can no longer use one capability to invoke another
registered tool. Tool capability identifiers become versioned service contract
data and must be reviewed when tools are added or renamed. `tools/list` keeps
the standard MCP schema; capability identifiers remain server policy rather
than untrusted tool-description metadata.

## Alternatives considered

- Check only `tool:execute`: rejected because it does not distinguish tools
  with different Contract capabilities.
- Infer capability from the tool name at request time: rejected because naming
  conventions are not an authorization registry.
- Put the mapping only in the Python Resolver: rejected because direct MCP
  callers would bypass the Harness.
- Reject all legacy SSE/STDIO calls immediately: deferred to a compatibility
  migration so existing MCP clients are not silently broken in this slice.

## Verification

- Registry tests cover matching, mismatch-before-handler, legacy compatibility,
  stable built-in mappings, and fail-closed registration.
- The Python-to-Go cross-process contract rejects `system_health` when the
  request declares `knowledge.search`.
- Existing MCP transport, IAM, Python adapter, and capability resolver suites
  remain green.

## Supersedes

None. This adds server-side enforcement to ADR-0020 and ADR-0022.
