# ADR-0030: Scope-Aware Agent Binding

> Status: accepted
> Owner: Mission Control maintainers
> Date: 2026-08-14
> Scope: Agent selection for delegated WorkUnits

## Context

`assigned_adapter` alone cannot identify both the configured Agent selected by
the user and the runtime adapter that will execute it. The legacy Agent
registry also contains provider credentials and raw configuration that must not
enter Mission, WorkUnit, event, MCP, or A2A payloads.

## Decision

Mission Control resolves a requested Agent through an injected
`AgentBindingResolver` port. A binding is scoped by the Mission workspace and
returns only:

- stable `agent_id`;
- runtime `adapter_type`;
- a capability snapshot.

The delegated WorkUnit persists `assignedAgentId` and the resolved
`assignedAdapter` separately. Required capabilities must be allowed by both the
Mission Contract and the binding snapshot. The default API resolver fails
closed with `503` until a durable catalog adapter is configured; unknown Agents
and insufficient binding capabilities are rejected without creating a child.

The current model uses `Mission.workspace_id` as the scope key because the
control plane does not yet carry an explicit tenant field. This is a temporary
identity mapping, not a default tenant, and must be replaced by an authenticated
tenant identity migration when that field is introduced.

## Consequences

Agent selection is auditable and replayable without copying credentials into
execution state. Adapter implementations can evolve behind a stable binding
contract. Delegation is temporarily unavailable in deployments that have not
installed a catalog adapter, which is safer than accepting an unverified Agent
ID.

## Verification

- API tests cover missing bindings and binding capability mismatch/fencing.
- Service tests prove scope isolation and that sensitive mapping fields are not
  represented by `AgentBinding`.
- WorkUnit v1 adds optional `assignedAgentId`; migration adds the nullable
  persistence column and index.
