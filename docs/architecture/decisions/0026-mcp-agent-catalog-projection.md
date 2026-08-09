# ADR-0026: MCP Agent Listing Uses a Safe Catalog Projection

> Status: accepted
> Owner: protocol and control-plane maintainers
> Date: 2026-08-09
> Scope: `list_agents` MCP tool and Gateway Agent catalog read path

## Context

The MCP Registry declared `list_agents` and called
`/platform/agent-registry`, but the Gateway did not expose that route. The
repository also contains three different Agent-shaped collections:

- user-configured records in the legacy `agent_registry` table;
- self-declared AgentNet runtime capabilities;
- static Realtime Orchestrator role descriptors.

Treating these as interchangeable would make discovery unstable and would
strengthen AgentNet as a competing source of truth. Returning the legacy table
verbatim would also expose provider URLs, encrypted credentials, raw config,
and avatar data to the model.

## Decision

`list_agents` represents the configured Agent catalog visible to the
authenticated actor. It does not represent active AgentNet peers or internal
orchestrator stages.

The MCP Registry requires verified tenant/actor identity and the verified
Bearer credential, injects the authenticated tenant query, and rejects
downstream non-2xx or malformed JSON responses.

Gateway exposes `GET /platform/agent-registry` behind shared IAM and the
`agent:read` scope. It ignores caller-provided identity, derives tenant and
actor from `TenantContext`, and queries the existing `agent_registry` table by
globally unique actor ID. A user-specific record overrides the same
system-default agent ID. The response is a fixed projection containing
identity, display, status, model label, risk, duty, and capability tags only.
API keys, base URLs, raw configuration, and avatar data are never selected.

This direct read is a migration compatibility boundary, not new Gateway-owned
business state. The table remains owned by the Python control-plane migration
surface until a tenant-native Agent Catalog is introduced.

## Consequences

The declared MCP tool now has a real authenticated route and cannot enumerate
another actor's configured agents. Catalog/database failures return explicit
errors instead of an empty synthetic roster. AgentNet and Realtime role tables
remain operational projections and do not become Agent catalog truth.

The legacy table has no `tenant_id`; isolation currently relies on the IAM
user ID being globally unique, as required by `platform_users.id`. A future
catalog migration must introduce explicit tenant ownership before supporting
cross-tenant user identities or shared organization catalogs.

## Alternatives considered

- Use AgentNet capabilities: rejected because self-declared runtime presence is
  not configured Agent availability and would reinforce a legacy scheduler.
- Use Realtime Orchestrator `/agents`: rejected because static loop roles are
  implementation details, not user-configured agents.
- Proxy the Python `/api/agent/registry` endpoint: rejected for this slice
  because its legacy user JWT is not the shared platform IAM credential.
- Return the complete database row: rejected because it contains secrets and
  internal provider configuration.

## Verification

- Registry tests verify tenant and credential propagation, fail-closed
  identity, downstream status handling, and malformed JSON rejection.
- Gateway tests verify actor-derived lookup, tenant projection, method
  rejection, unavailable-store behavior, and capability-tag normalization.
- The fixed response type makes sensitive database columns unavailable to the
  encoder by construction.
