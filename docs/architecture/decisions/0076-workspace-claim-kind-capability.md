# ADR-0076: Workspace Claim WorkUnit Kind Capability

> Status: accepted  
> Owner: Mission Control and Runner maintainers  
> Date: 2026-08-21  
> Scope: workspace work discovery and Runner execution compatibility

## Context

A workspace can contain ready WorkUnits with the same Agent and adapter binding
but different execution kinds. Binding identity alone does not prove that one
Runner process has the resolver and Harness composition required for every
kind. Rejecting a kind after claim is unsafe because the claim has already
created an attempt and lease and can turn compatible work into a failure.

## Decision

Every workspace-scoped claim request carries a non-empty, unique, bounded
`supportedWorkUnitKinds` list. Mission Control treats this list as transient
Runner capability metadata and filters candidates by kind in the transactional
repository query before ordering, locking, or leasing a WorkUnit.

The list is not persisted and does not modify Mission, Contract, WorkUnit,
catalog binding, or tenant quota truth. Agent ID and adapter remain independent
binding constraints. Mission-scoped claims are unchanged because their caller
has already selected a Mission and their existing root/delegation rules remain
authoritative.

Missing, empty, duplicate, oversized, or invalid lists fail before claim
authorization and repository access. A Runner composition with workspace
claims enabled must declare its supported kinds explicitly. Inbound A2A
declares `a2a.inbound`; outbound A2A declares `a2a.delegate`. Mission-fork
workspace polling remains disabled until a kind-aware resolver router is
composed.

## Consequences

Mission Control cannot lease a WorkUnit that the requesting Runner has not
declared executable. Unsupported candidates remain untouched and can be
claimed by another compatible Runner. Adding a new WorkUnit kind requires an
explicit Runner composition and capability declaration rather than inheriting
an unsafe default.

This is admission filtering, not authorization. Contract capability grants,
tool resolution, workspace ACLs, tenant concurrency, lease fencing, and
execution-context validation continue to apply independently.

## Alternatives considered

- Filter after claim in Runner: rejected because it consumes an attempt and
  lease before compatibility is known.
- Infer support from adapter type: rejected because one adapter can host
  multiple resolver/Harness compositions.
- Persist Runner kinds in the Agent catalog: deferred because process rollout
  capability can differ from the durable binding snapshot and would introduce
  stale scheduling truth.
- Treat a missing list as all kinds: rejected because old or partial Runner
  deployments would continue claiming work they cannot execute.

## Verification

Schema and API tests reject malformed declarations without authorization or
state mutation. Service tests prove unsupported candidates remain pending.
Repository tests require kind filtering before `FOR UPDATE`; PostgreSQL tests
must prove concurrent claims cannot lease an unsupported kind.
