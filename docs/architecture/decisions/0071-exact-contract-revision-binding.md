# ADR-0071: Exact Contract Revision Binding

> Status: accepted  
> Owner: Mission Control maintainers  
> Date: 2026-08-16  
> Scope: Contract identity, Mission persistence, and execution projections

## Context

MissionContract already carried an integer `version`, but persistence used only
Contract `id` as the primary key and Mission stored only `contractId`. That made
the version descriptive rather than referential: supporting another immutable
revision under the same logical Contract identity would make Mission reads
ambiguous.

Decision SLA, capability grants, budgets, acceptance criteria, and verifier
policy all depend on the exact Contract used when a Mission was admitted. A
consumer must never infer that revision from whichever Contract is newest.

## Decision

Contract `id` is the stable lineage identity. `(id, version)` is the immutable
revision identity. Mission stores both `contractId` and `contractVersion`, and
the database enforces a composite foreign key to the matching Contract row.

The migration backfills each existing Mission from the previously unique
Contract row before replacing the Contract primary key. It fails if a Mission
cannot be bound. Downgrade fails when multiple revisions exist because reducing
them to an ID-only primary key would lose valid history.

Repository reads require both identity components. Mission Control, Runner,
verifier, Decision supervision, and A2A projections carry and validate the exact
pair. New public Mission responses include `contractVersion`; the property is
optional in the v1 JSON Schema only to preserve validation of historic v1
documents.

Ordinary Mission creation may create only a version 1 lineage. This ADR adds no
Contract update or Mission rebind operation. Later revisions require a separate
command with concurrency control, ancestry validation, and explicit lifecycle
rules.

## Consequences

Existing Missions remain bound to the Contract content under which they were
created. Multiple immutable revisions can coexist without latest-version reads,
and a running Mission cannot silently acquire changed policy.

Callers that consume new Mission projections should retain `contractVersion`.
Internal execution consumers reject a missing or mismatched version instead of
falling back to version 1.

## Alternatives considered

- Use a separate opaque revision ID: rejected because the existing public model
  already defines versioned identity and another identifier would duplicate it.
- Resolve the greatest version on every read: rejected because it silently
  changes policy for active and historical Missions.
- Copy the complete Contract into every Mission row: rejected because it creates
  a second persistence representation and weakens referential integrity.
- Permit Mission rebinding: rejected because it changes delegated terms and
  invalidates audit and Evidence provenance.

## Verification

Domain and public-contract tests cover the version projection. Repository tests
verify exact `(id, version)` reads and writes. Migration tests cover backfill,
composite keys, guarded downgrade, startup ordering, and head idempotency.
Runner and A2A tests reject identity drift between Mission and Contract.
