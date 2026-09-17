# ADR-0051: Runner Claim Outcome Observability

> Status: accepted  
> Owner: Mission Control and Runner maintainers  
> Date: 2026-08-15  
> Scope: workspace claim response and Runner operational state

## Context

ADR-0050 introduced tenant concurrency admission but represented both quota
saturation and absence of ready work as `workUnit: null`. The shared response
kept polling safe, but operators could not distinguish normal idle capacity
from a tenant limit that was preventing new execution. Inferring the reason in
Runner would require copying quota policy or durable WorkUnit state and would
create a second scheduling authority.

## Decision

Mission Control returns a low-cardinality `claimStatus` with every successful
workspace or Mission-scoped bound claim:

- `claimed`: the response contains exactly one leased `workUnit`.
- `idle`: no eligible ready WorkUnit was selected.
- `capacity_saturated`: tenant concurrency admission rejected the claim.

The existing `workUnit` field remains present. It is non-null only for
`claimed`; malformed combinations fail Runner response validation before
context resolution or Harness execution. Authorization and policy failures
remain explicit `403` or `503` responses and are not converted into claim
statuses.

The response is defined by the additive v1
`work-unit-claim-response.schema.json` contract. Existing consumers that read
only `workUnit` remain compatible. The workspace Runner consumes the stronger
contract and returns an explicit process-local poll result to its supervisor.

Runner readiness remains true after any valid claim response, including idle
and saturation. Its sanitized snapshot records separate cumulative idle and
saturation counters plus the last successful claim status. These values reset
on process restart and never become Mission events, quota counters, scheduling
cursors, or database state. Tenant IDs, configured limits, active counts, and
WorkUnit content are excluded from the snapshot to avoid sensitive or
high-cardinality operational data.

## Consequences

Operators can distinguish an idle Runner from capacity pressure without
granting Runner access to IAM quota tables or Mission queries. Backoff behavior
is unchanged for both empty outcomes, so observability does not alter dispatch
fairness or create a retry loop.

During rollback, an older Runner ignores the additive `claimStatus` field and
continues treating both empty outcomes as idle. A newer Runner requires the v1
status field and fails unready against an older Mission Control response rather
than inventing a reason. Deploy Mission Control before Runner when rolling
forward, and Runner before Mission Control when rolling back.

## Verification

Contract tests validate all three statuses and reject status/payload mismatch.
API and PostgreSQL integration tests distinguish claimed, idle, and saturated
transactions while retaining the no-lease/no-event saturation assertions.
Runner tests reject malformed response combinations before execution and prove
that readiness snapshots count idle and saturation separately without exposing
exception content or scoped identifiers.
