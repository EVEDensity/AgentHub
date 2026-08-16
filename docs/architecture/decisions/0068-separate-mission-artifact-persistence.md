# ADR-0068: Separate Mission Artifact Persistence

> Status: accepted  
> Owner: Mission Control and legacy application maintainers  
> Date: 2026-08-16  
> Scope: PostgreSQL table ownership for Artifact metadata

## Context

The initial AgentHub schema owns an `artifacts` table containing versioned
session files and inline content. Mission Control later attempted to create a
different `artifacts` table for immutable execution metadata. PostgreSQL's
`CREATE TABLE IF NOT EXISTS` accepted the existing name without validating its
columns, so a fresh Alembic chain failed when it indexed the missing
`mission_id` column.

The two records have different owners, lifecycles, authorization boundaries,
and byte-storage behavior. Combining their columns would preserve the name but
would create an ambiguous table and couple legacy content to Mission Control.

## Decision

The legacy application retains `artifacts` for session file versions. Mission
Control stores Artifact metadata in `mission_artifacts`, and its Repository is
the only production owner of that table. Artifact bytes remain in Runner-owned
content-addressed storage.

Fresh installations create both tables under their distinct names. A new head
migration handles databases that already completed the earlier Mission
Artifact migration: when `artifacts` has both `mission_id` and `work_unit_id`
and `mission_artifacts` is absent, it renames the table, primary-key constraint,
and indexes in place. Renaming the primary-key index releases the `artifacts_pkey`
name for the legacy table. When `artifacts` is the legacy session table, it is
not altered. If both
names exist while `artifacts` also has the Mission schema, migration fails
closed instead of choosing between two possible sources of truth.

The downgrade renames Mission metadata back only when the legacy table name is
free. Otherwise it retains `mission_artifacts` to avoid overwriting or dropping
either dataset. A later full downgrade removes it through the original Mission
Artifact revision.

## Consequences

Fresh migration no longer depends on table creation order, and legacy Artifact
content is neither copied nor rewritten. Mission Control queries become
unambiguous and can evolve independently from the legacy API.

Operators upgrading a database with an unexpected dual Mission schema receive
an explicit migration error and must reconcile ownership before retrying. The
name change is transactional on PostgreSQL but still requires deploying the
migration before code that queries `mission_artifacts` begins serving traffic.

## Alternatives considered

- Add Mission columns to the legacy table: rejected because it mixes content,
  metadata, authorization, retention, and foreign-key lifecycles.
- Rename the legacy table: rejected because the established API owns that name
  and Mission Control is the newer persistence boundary.
- Copy Mission rows into a second table: rejected because it creates a partial
  failure window and unnecessary duplicate data.
- Drop and recreate `artifacts`: rejected because it destroys user data.

## Verification

Migration tests protect the distinct table names and the old-head upgrade path.
The isolated Decision expiry smoke gate runs the complete Alembic chain against
a fresh PostgreSQL database and therefore verifies the original collision is
absent in a real engine.

## Extends

This decision clarifies the Mission Control persistence ownership established
by the repository architecture and unblocks the smoke gate in
[ADR-0067](0067-isolated-decision-expiry-smoke-gate.md).
