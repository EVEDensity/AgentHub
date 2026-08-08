# ADR-0008: Transactional Evidence Projection

> Status: accepted  
> Owner: architecture maintainers  
> Date: 2026-08-08  
> Scope: Evidence persistence, Mission success gating, migrations, and query API

## Context

Evidence was durable only as an event payload. Reconstructing it from the event
ledger made user queries depend on audit-envelope layout and made Mission
success use a paginated list capped at 200 rows. A Mission with enough history
could therefore miss a required PASS criterion even though the Evidence was
durable.

Existing deployments may already contain Evidence events, so introducing an
empty projection without a backfill would also hide valid historical results
after upgrade.

## Decision

Mission Control owns an append-only Evidence projection alongside the immutable
event ledger.

- A verifier command writes the Evidence row, Evidence event, WorkUnit update,
  and any Mission update in one database transaction.
- Evidence rows are immutable. This slice provides add, get, and bounded
  Mission-list operations but no update operation.
- Mission success uses an unbounded `DISTINCT criterion_id` query restricted to
  PASS Evidence. User-facing listing remains paginated and cannot affect state
  transitions.
- The authorized Mission API exposes the projection for review and audit
  navigation. Verifier role and artifact-reference checks remain unchanged.
- The migration backfills `evidence.lifecycle.recorded` payloads with
  `ON CONFLICT DO NOTHING`, then advances the schema revision. The event ledger
  remains the append-only audit history and is not deleted or rewritten.

## Consequences

Evidence can be queried without decoding event envelopes, and success gating no
longer depends on a presentation limit. Projection and lifecycle state cannot
diverge when the real repository transaction rolls back. Storage is duplicated
between the normalized projection and event payload by design; reconciliation
and projection rebuild tooling remain future operational work.

The supplied `integrityHash` is persisted and schema-validated, but this ADR
does not claim independent cryptographic verification of that hash.

## Alternatives considered

- Continue querying Evidence events directly: rejected because audit storage
  and business queries have different indexing and pagination requirements.
- Build the projection asynchronously: deferred because eventual consistency
  could make a just-recorded PASS invisible during the same success decision.
- Create an empty projection and ignore historical events: rejected because an
  upgrade must preserve existing Evidence semantics.

## Verification

- Migration tests cover every supported prior revision and Artifact-head-only
  upgrade, including the event-ledger backfill statement.
- Repository tests cover add/get/list round trips and the unbounded PASS
  criterion query.
- API tests cover authorized Evidence listing and success with more than 200
  historical Evidence rows.

## Supersedes

The Evidence-storage deferral in ADR-0004.
