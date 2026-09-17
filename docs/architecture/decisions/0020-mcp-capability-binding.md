# ADR-0020: MCP Capability Binding

> Status: accepted
> Owner: architecture maintainers
> Date: 2026-08-09
> Scope: Contract-scoped MCP tools in Harness

## Context

ADR-0016 made Harness function tools least-privilege by resolving the
intersection of Contract grants and WorkUnit requirements. ADR-0019 added a
stateless MCP client, but constructing that client with a fixed scope could
allow callers to bypass the resolver or accidentally reuse one WorkUnit's
scope for another.

## Decision

Add `build_mcp_capability_binding`, which creates the existing
`CapabilityToolBinding` shape around an `MCPClientPort`. The resolver remains
the only component that decides whether a capability is granted and supplies
its scope. The binding constructs a fresh `MCPCallContext` for each tool call
using the resolver-provided scope and the request's immutable
`HarnessExecutionContext`.

The binding performs only object-shape validation. Capability authorization,
tool selection, duplicate-name checks, and scope policy remain in
`CapabilityToolResolver`. MCP errors remain tool failures and are returned to
the Harness loop as structured feedback.

## Consequences

MCP tools now use the same least-privilege path as local FunctionTools without
adding an MCP-specific registry or second authorization model. Scope changes
are visible at call construction and cannot be inherited from a previous
execution. The binding still requires the caller to construct it with the
current WorkUnit attempt; it does not discover or persist Mission state.

## Alternatives considered

- Let `StatelessMCPToolAdapter` choose its own Contract scope: rejected because
  protocol adapters must not become authorization sources.
- Add MCP-specific grants beside `CapabilityToolBinding`: rejected because it
  would create two capability resolution paths.
- Pass scope only in tool arguments: rejected because untrusted model input
  could alter authorization context.

## Verification

- Capability resolver tests prove the MCP binding receives the authorized
  Contract scope and Mission/WorkUnit/attempt context at call time.
- Existing MCP adapter, Harness, Runner, Artifact, Mission API, persistence,
  migration, and A2A regression suites remain green.

## Supersedes

None. This extends ADR-0016 and ADR-0019.
