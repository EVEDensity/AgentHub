# ADR-0050: Tenant Runner Concurrency Admission

> Status: accepted  
> Owner: IAM, Mission Control, and Runner maintainers  
> Date: 2026-08-15  
> Scope: workspace claim admission and execution concurrency

## Context

Workspace discovery can safely lease distinct WorkUnits to multiple Runner
principals, but it previously had no governed concurrency ceiling. A
Runner-local semaphore would apply only to one process, disappear on restart,
and create a second scheduling authority. The legacy AgentNet
`current_load/max_concurrent` fields are also unsuitable because they are
mutable scheduler state outside Mission Control.

IAM already owns plan quota truth in `platform_quota_definitions` and tenant
overrides in `platform_tenants.quotas_json`. Workspaces map to tenants through
`platform_workspaces`. Mission Control owns the authoritative WorkUnit states
needed to measure actual Runner execution.

## Decision

Before every Runner claim, Mission Control resolves the workspace tenant and
effective `max_concurrent` from the existing IAM quota tables. This applies to
workspace discovery and the Mission-scoped compatibility endpoint, so the old
path cannot bypass quota. Tenant overrides take precedence over plan defaults.
A limit of `0` means unlimited. Missing or malformed quota data and database
failures return `503`; a tenant whose status is not `active` returns `403`.
Admission never falls back to an invented limit or allow-by-default behavior.

For a positive limit, the claim transaction takes a tenant-scoped PostgreSQL
transaction advisory lock, counts non-expired WorkUnits in `LEASED` or `RUNNING`
state across all of that tenant's workspaces, and returns an empty claim when
the count has reached the limit. Selection, lease creation, and event append
remain in the same transaction. No usage counter, scheduler row, queue, or
Runner-local capacity state is introduced.

`VERIFYING` does not consume Runner execution capacity because Runner has
already completed that attempt. Expired leases do not consume capacity either;
their WorkUnits still require the existing Mission Control recovery path before
they can be reclaimed.

When the effective limit is `0`, Mission Control skips the admission lock and
count so the existing concurrent `SKIP LOCKED` discovery path is unchanged.

## Consequences

Finite tenant limits cannot be exceeded by concurrent workspace claims through
write skew. Claim transactions for one bounded tenant are briefly serialized,
but WorkUnit execution is not serialized; different tenants proceed
independently. The lock is transaction-scoped and leaves no durable scheduler
state after commit or rollback.

At capacity, the polling contract returns `workUnit: null`. Runner uses its
normal bounded empty-claim backoff and remains ready. Distinguishing idle from
quota saturation in operational metrics remains a follow-up observability
slice; it must not change durable Mission state.

## Verification

Policy tests cover plan resolution, tenant overrides in the query, unlimited
limits, inactive tenants, malformed data, and unavailable storage. Repository
tests verify tenant-scoped transaction locking and a live lease count that
excludes `VERIFYING` and expired attempts. API tests prove capacity returns an
empty claim without lease or event writes and that policy/state failures fail
closed. PostgreSQL integration retains the forced unlimited `SKIP LOCKED` test
and adds a bounded concurrent test where two Runner requests under a limit of
one produce exactly one active lease and one lease event.
