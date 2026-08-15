# ADR-0047: Workspace Ready-Work Discovery

> Status: accepted  
> Owner: Mission Control and Runner maintainers  
> Date: 2026-08-15  
> Scope: ready-work discovery, authorization, fairness, and row locking

Tenant concurrency admission now extends this selection policy through
[ADR-0050](0050-tenant-runner-concurrency-admission.md).

## Context

The minimum Runner process introduced by ADR-0046 must be configured with one
Mission ID because the only claim API is Mission-scoped. Making Runner list or
scan Missions would move scheduling policy to the execution edge, create a race
between discovery and claim, and eventually become a second queue. Mission
Control already owns workspace authorization, dependency truth, binding
snapshots, leases, and transactional WorkUnit events, so discovery belongs
there.

A workspace can contain multiple RUNNING Missions for the same Agent binding.
Simple Mission-ID ordering can starve later Missions when an earlier Mission
continues producing delegated work. A new durable scheduler table or mutation
of Mission `updated_at` would add state before the minimum chain proves it is
needed.

## Decision

Expose `POST /api/v1/missions/work-unit-claims` with explicit `workspaceId`,
`agentId`, `adapterType`, and bounded `leaseSeconds`. The authenticated token,
not request content, supplies the Runner lease owner and event actor. Workspace
authorization runs before discovery.

Mission Control selects one candidate in the same transaction that creates its
lease and appends the WorkUnit event. The repository query:

- restricts Missions to the authorized workspace and `RUNNING` state;
- admits delegated WorkUnits, or root `a2a.inbound` WorkUnits only when the
  owning Mission source is also `a2a.inbound`;
- matches the immutable assigned Agent and adapter snapshot;
- accepts only `PENDING` or `RETRYING` units with all dependencies `SUCCEEDED`;
- orders by the count of `LEASED`, `RUNNING`, and `VERIFYING` units in each
  Mission, then Mission creation time, Mission ID, and WorkUnit ID;
- locks both Mission and WorkUnit rows with `FOR UPDATE ... SKIP LOCKED`.

The application service revalidates workspace, Mission, binding, root shape,
and dependencies as defense in depth before leasing. An empty discovery result
returns `workUnit: null` and performs no durable write. The existing
Mission-scoped claim remains available during Runner migration.

## Consequences

Runner can eventually poll one workspace without listing Missions or owning a
queue. Concurrent claimers skip locked work, while least-in-flight ordering
spreads unverified and active attempts across eligible Missions without new
scheduler state. This is deterministic load-aware fairness, not weighted tenant
priority or a strict round-robin guarantee.

The strict Runner process still uses its fixed-Mission worker until a separate
consumer migration switches it to this contract. Future priority, quotas, or
capacity routing must extend Mission Control policy and remain transactional;
they must not be implemented as Runner-local filters.

## Verification

Repository tests verify workspace, Mission state, inbound-root, binding,
dependency, least-in-flight ordering, and dual-row `SKIP LOCKED` clauses. API
tests cover fair selection, authorization rejection, repository scope escape,
lease identity, event correlation, and existing Mission-scoped behavior. Client
tests verify the exact credentialed request without a Mission scan.
