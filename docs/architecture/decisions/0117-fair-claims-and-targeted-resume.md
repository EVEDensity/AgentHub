# ADR-0117: Fair workspace claims and explicit recovery targets

> Status: implemented
>
> Owner: Mission Control and Runner maintainers
>
> Last reviewed: 2026-10-10
>
> Scope: existing bounded local execution and authenticated claim protocol

## Context

Real SQLite and HTTP acceptance uncovered three gaps in the execution route.
Desktop workers generated local owner suffixes that the authenticated API did
not recognize. Discovery repeatedly returned a busy owned attempt before a
ready sibling. Tenant admission also rejected an existing lease at full quota,
while SQLite counted expired leases differently from PostgreSQL.

Prioritizing ready work alone could delay explicit CLI recovery until its live
lease expires. Recovery needs an exact target without weakening lease fences.

## Decision

All local workers use their authenticated principal as lease owner. Multiple
workers remain bounded by existing configuration, tenant admission and the
private attempt execution lock; no new worker identity authority is introduced.

Normal workspace discovery orders ready PENDING/RETRYING candidates before
owned live LEASED/RUNNING candidates, then retains load and deterministic
ordering. Existing binding, dependency, kind and workspace predicates remain.

The version-1 workspace claim request adds optional `resumeMissionId`. When
present, discovery selects only the requested RUNNING Mission's same-owner,
unexpired LEASED/RUNNING unit. It returns the original attempt and lease without
renewing or replacing them. Missing, foreign, expired or PENDING-only targets
return `idle`; there is no fallback to ordinary discovery. Public responses
retain their existing shape. The complete private image and receipt restore
remains a second execution gate under ADR-0115.

The CLI passes this target before starting its server for execution recovery.
Compact-context chat chaining starts a new turn and keeps normal polling.
Inherited target variables cannot redirect an ordinary CLI invocation.

Tenant admission retains tenant-before-candidate lock ordering. Full capacity
blocks a new lease, but reclaiming an existing live lease does not add capacity.
Both database backends exclude expired leases from active concurrency counts.
Unavailable admission policy or state still fails closed.

## Verification and boundaries

Real local boot and HTTP tests run two workers concurrently and inspect actual
leases, terminal checkpoints and registered CAS artifacts. A child process holds
an attempt lock while a ready sibling reaches VERIFYING. SQLite/PostgreSQL tests
cover target selection, unchanged lease identity, quota and expiry, and reject
foreign or unrelated claims. Contract and CLI tests protect the additive wire
field and pre-start target propagation.

This does not introduce automatic expired-lease recovery or cross-attempt
replay. Ordinary polling favors ready work; operators needing a specific owned
attempt should use explicit recovery. These are bounded acceptance results,
not production-environment verification.
