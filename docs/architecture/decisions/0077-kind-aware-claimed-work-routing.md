# ADR-0077: Route Claimed Work by Durable WorkUnit Kind

> Status: accepted  
> Date: 2026-08-21  
> Owners: execution maintainers

## Context

Workspace polling can admit more than one executable root kind for the same
Agent and adapter binding. A Runner that advertises kinds separately from its
resolver registrations can lease work that it cannot interpret, or can send a
fork projection through the inbound A2A compiler.

## Decision

Model-backed workspace execution uses an immutable kind-to-resolver registry.
The exact sorted registry keys form the transient `supportedWorkUnitKinds`
claim declaration. Dispatch reads the durable claimed WorkUnit `kind` and has
no default resolver. Unknown and malformed kinds fail closed.

The first mixed composition registers only `a2a.inbound` and `mission.fork`.
Each kind retains its own context profile and Harness policy. Outbound A2A is
not registered because it uses its native transport supervisor rather than the
model Harness path.

## Consequences

- Claim capability and executable resolver coverage have one composition-time
  source of truth.
- A shared Agent/adapter binding cannot cause cross-kind compiler reuse.
- Adding another workspace-executable kind requires an explicit resolver
  registration and integration coverage.
- Production Runner service wiring remains unchanged until its separate
  cutover gate is completed.
