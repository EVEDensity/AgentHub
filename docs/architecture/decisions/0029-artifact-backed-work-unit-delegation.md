# ADR-0029: Artifact-Backed WorkUnit Delegation

> Status: accepted
> Owner: Mission Control maintainers
> Date: 2026-08-09
> Scope: durable delegation between local WorkUnits

## Context

An Agent call can occur while a parent WorkUnit is running, but a raw prompt or
session message is not durable execution input. The child must be reconstructable
after a Runner or control-plane restart, and a stale Runner must not create work
after its lease has expired.

## Decision

Mission Control exposes a delegation command that atomically creates a child
WorkUnit from immutable, already-registered `ArtifactRef` inputs. The command:

- requires the parent WorkUnit to be `RUNNING` and fenced by its current lease
  and runner identity;
- validates every input reference against the same Mission and stored digest;
- stores `parentWorkUnitId` as a separate relationship, not as a dependency;
- validates requested capabilities against the Mission Contract;
- resolves the target Agent through a scope-aware binding port and intersects
  its capability snapshot with the requested capabilities;
- records `work_unit.delegation.requested` on the parent and a causally linked
  `work_unit.lifecycle.created` event for the child;
- requires an explicit child WorkUnit ID so a retried request can return the
  same immutable delegation instead of creating a second child.

The HTTP endpoint returns `202 Accepted` with the durable child WorkUnit. It
does not claim that the delegated Agent has started or completed.

## Consequences

Delegated inputs have a durable byte identity and can be replayed by Runner and
Harness. Parent and child execution can be scheduled independently because the
parent relationship is not a dependency edge. MCP and A2A adapters can later
map their requests to this command without owning Mission state.

The default API deployment fails closed until a durable scope-aware Agent
catalog adapter is installed. The current control-plane model has
`workspaceId`, not an explicit tenant field, so the binding port receives the
Mission workspace as its scope key. A later tenant identity migration must
replace that key without changing the WorkUnit contract. This slice still does
not execute the child; scheduler/Runner work is a separate stage.

## Verification

- API tests cover active lease fencing, ArtifactRef validation, causal events,
  and idempotent retry by child ID.
- Migration tests cover advancing the existing Mission Control head with the
  parent WorkUnit relationship column and index.
- The v1 WorkUnit schema accepts the additive optional `parentWorkUnitId`.
