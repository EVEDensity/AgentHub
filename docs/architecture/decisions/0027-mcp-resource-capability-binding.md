# ADR-0027: MCP Resources Use Static Capability Bindings

> Status: accepted
> Owner: protocol and execution maintainers
> Date: 2026-08-09
> Scope: MCP Registry resource discovery and stateless resource reads

## Context

The Stateless MCP transport validates Mission, WorkUnit, attempt, capability,
and scope metadata before dispatch. Tool registration already binds each tool
to one capability, but Resource registration previously stored definitions
only. A caller with any authorized MCP execution context could therefore read
any registered Resource because the declared capability was never compared
with the selected Resource.

Resource fetching also ignored downstream HTTP status and body-read errors.
The `agenthub://agents/manifest` Resource called Gateway without the verified
Bearer credential or tenant, so it could not use the authenticated Agent
Catalog projection introduced by ADR-0026.

## Decision

Every built-in MCP Resource is registered with exactly one static Contract
capability:

| Resource | Capability |
|---|---|
| `agenthub://knowledge/collections` | `knowledge.read` |
| `agenthub://agents/manifest` | `agent.read` |
| `agenthub://templates/catalog` | `template.read` |
| `agenthub://workspaces/list` | `workspace.read` |

For requests carrying a validated stateless execution context, Registry
requires the declared capability to exactly match the Resource binding before
running a handler or making a network request. Calls without an execution
context retain the existing SSE and STDIO compatibility behavior; they are not
the protected Harness boundary.

Agent Manifest and the `list_agents` Tool share one authenticated catalog
fetch path. It derives tenant and actor from verified IAM context, forwards the
verified Bearer credential, injects the authenticated tenant, rejects non-2xx
responses, and requires valid JSON. Other JSON Resources now also reject
request construction failures, non-2xx responses, body-read failures, and
malformed JSON without changing their existing identity model.

## Consequences

Resource reads can no longer use an unrelated Contract capability to bypass
the Registry authorization boundary. Agent discovery has one transport-neutral
security implementation, reducing drift between Tool and Resource behavior.
Registration fails closed when a URI or capability is missing or duplicated.

The capability names become part of the versioned MCP contract. Adding or
renaming a Resource capability requires a contract and compatibility review.
Legacy transports remain less constrained and must not be used for protected
WorkUnit execution.

## Alternatives considered

- Authorize Resources only with `tool:execute`: rejected because that scope
  permits MCP use but does not grant every Resource capability.
- Trust the capability header without a Registry binding: rejected because
  execution metadata is a requested scope, not proof that a Resource belongs
  to that capability.
- Implement Agent Manifest as a second catalog client: rejected because Tool
  and Resource behavior would drift and could expose different fields.

## Verification

- Registry tests verify stable Resource bindings and fail-closed registration.
- Mismatch tests prove handlers and downstream services are not reached.
- Agent Manifest tests verify tenant and credential propagation, missing
  identity and credential failures, non-2xx handling, and JSON validation.
- Compatibility tests verify legacy calls without execution context still use
  the registered Resource.
