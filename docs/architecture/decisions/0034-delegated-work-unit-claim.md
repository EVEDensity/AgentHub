# ADR-0034: Claim Delegated WorkUnits Through Mission Control

> Status: accepted  
> Owner: Mission Control maintainers  
> Date: 2026-08-14  
> Scope: Mission repository, Mission API, delegated Runner execution

## Context

Delegation creates child WorkUnits with an assigned Agent and adapter, but the
existing Runner entry point only leases a WorkUnit when its ID is already known.
That leaves scheduling to an external caller and risks a second queue or task
state. Claims also need to be safe when multiple Runner instances poll one
Mission.

## Decision

Mission Control exposes a Mission-scoped `POST /work-unit-claims` command. The
command locks the Mission first, then selects one delegated WorkUnit whose
assigned Agent and adapter match the request, whose status is `PENDING` or
`RETRYING`, and whose dependencies are all `SUCCEEDED`. PostgreSQL uses
`FOR UPDATE SKIP LOCKED`; the selected row is transitioned to `LEASED`, given a
new fenced lease and attempt, and recorded by the existing lifecycle event in
one transaction.

Runner adds a fixed Agent/adapter binding and a `claim_and_run` entry point. A
claim response must identify the requested Mission and binding, be `LEASED`,
and carry a lease owned by that Runner. A replaceable resolver must convert
durable WorkUnit references into bounded execution input. Without that resolver
the Runner fails the claimed unit honestly and never invents executable input.

## Consequences

Mission Control remains the only scheduler and durable source of truth. Polling
Runners can scale horizontally without duplicate claims, and delegated work
uses the same lease, heartbeat, artifact, and completion protocol as direct
work. A context or artifact resolver is required before delegated execution can
produce output; this is an intentional stop line for the next phase.

## Alternatives considered

- A new queue or broker was rejected because it would duplicate WorkUnit state
  and require another recovery protocol.
- A cross-Mission claim endpoint was rejected because it would weaken the
  existing Mission-first lock ordering and workspace authorization boundary.
- Inferring code from an ArtifactRef or WorkUnit text was rejected because it
  cannot prove provenance or authorization.

## Verification

- Repository tests assert binding filters, dependency gating, and `SKIP LOCKED`.
- API tests assert atomic lease/attempt/event behavior and empty-claim semantics.
- Runner tests assert binding validation, strict claim response validation,
  resolver-mediated execution, and honest failure without a resolver.

## Supersedes

None.
