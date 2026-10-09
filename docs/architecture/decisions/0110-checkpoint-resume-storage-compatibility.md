# ADR-0110: Preserve checkpoint migration lineage and add resume metadata

> Status: accepted
> Owner: architecture maintainers
> Date: 2026-10-09
> Scope: checkpoint SQL migrations, SQLite initialization, public v1 projection

## Context

The original checkpoint migration file acquired a new revision ID whose parent
was its own former ID. Alembic could no longer resolve the graph. Existing
SQLite version-2 profiles returned before adding resume columns, and the
public schema rejected fields already produced by the domain and repository.

## Decision

- Restore the immutable `a6d0e1f2b3c4` revision and its original table shape.
- Add nullable resume metadata through separate `b7e1f203c4d5` SQL shared by
  Alembic and the runtime migration path. The head remains `b7e1f203c4d5` for
  compatibility with databases already stamped by the previous runtime.
- Upgrade SQLite version 2 to 3 by inspecting existing columns and adding only
  missing ones. Apply those changes and the schema marker transactionally;
  preserve existing values and fail startup on migration errors.
- Add the five optional fields to the public v1 schema. Older projections stay
  valid. A pending action requires a resume protocol version; existing domain
  bounds and execution-scoped key syntax apply.
- Never backfill fingerprints or pending actions for historical rows. A schema
  upgrade does not grant execution authority or prove that a checkpoint can
  safely resume. Existing lease, context, workspace and receipt gates remain.

## Consequences

New installations and older recorded heads have a resolvable, additive upgrade
path. Rolling PostgreSQL back to `a6d0e1f2b3c4` removes only resume metadata,
preserving original checkpoints; strict resume rejects the resulting legacy
rows. SQLite rollback uses a stopped-process database backup. Neither rollback
may be treated as permission to replay side effects.

Mission Control retains metadata ownership, Runner retains Artifact bytes,
and Harness retains tool arguments/results. No execution or memory owner changes.

## Alternatives considered

- Restamping every database was rejected because it disguises missing DDL.
- Replaying all SQLite seeds was rejected because it can overwrite user state.
- Filling historical fingerprints from today's workspace was rejected because
  it creates false recovery evidence.

## Verification

Contract tests validate both legacy and current domain projections. Real
SQLite tests cover preservation, partially added fields, idempotency, missing
tables, and failure rollback. Disposable PostgreSQL tests run the complete
Alembic chain, original-head upgrade, downgrade/reupgrade, injected failure,
runtime migration and repository roundtrip. CI runs the migration tests with
a PostgreSQL service. These checks do not establish production resume readiness.

## Supersedes

None. Repairs the persistence/projection implementation of the existing
checkpoint and production CLI boundaries.
