# ADR-0031: Durable Agent Catalog Projection

> Status: accepted
> Owner: Mission Control maintainers
> Date: 2026-08-14
> Scope: workspace-scoped Agent binding lookup

## Context

ADR-0030 introduced a scope-aware Agent binding port but left the production
resolver unavailable. The legacy `agent_registry` is scoped by user identity
and contains provider credentials and raw configuration. It cannot safely
resolve a Mission workspace, and treating a user ID as a workspace or tenant
would recreate the default-scope ambiguity already removed from protocol
adapters.

The repository also references `platform_agent_registry`, but no complete,
versioned table and writer contract exists for it. Depending on that implicit
schema would make Mission delegation deployment-dependent and untestable.

## Decision

Add `agent_catalog_bindings` as a credential-free, workspace-scoped projection.
Each row contains only:

- `scope_id` and `agent_id`;
- runtime `adapter_type`;
- capability snapshot;
- enabled flag, source version, and update timestamp.

Mission Control reads enabled rows through `DatabaseAgentBindingResolver`.
Unknown or disabled rows resolve as absent. Database failures, malformed rows,
and identity mismatches fail closed as catalog unavailability. The Mission API
uses this resolver by default and never falls back to the legacy user-scoped
registry.

This table is configuration projection, not execution truth. Mission,
Contract, WorkUnit, Artifact, Evidence, Decision, and Outcome remain the only
durable work model. Catalog write and synchronization ownership will be added
separately; this change intentionally adds no public mutation API.

## Consequences

Delegation can resolve durable workspace bindings without exposing provider
secrets or inventing a tenant. Deployments must explicitly populate the safe
projection before delegated Agents become available. This is an honest
operational dependency and keeps migration from the legacy registry explicit.

## Verification

- Resolver tests cover scope forwarding, unknown bindings, malformed identity,
  JSON capability parsing, and catalog failure behavior.
- Migration tests cover fresh installation, upgrade from the prior head, and
  idempotency at the new head.
- API tests verify that production dependency wiring selects the durable
  resolver while request tests remain database-isolated.
