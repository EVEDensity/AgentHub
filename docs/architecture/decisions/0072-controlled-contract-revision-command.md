# ADR-0072: Controlled Contract Revision Command

> Status: accepted  
> Owner: Mission Control maintainers  
> Date: 2026-08-16  
> Scope: Contract revision writes, concurrency, and workspace authorization

## Context

ADR-0071 made `(contractId, contractVersion)` an exact immutable identity but
intentionally left later revisions without a write path. Allowing clients to
insert arbitrary versions through ordinary Mission creation would bypass
lineage concurrency, workspace authorization, and audit requirements.

Contract lineage ownership is not yet materialized on `mission_contracts`.
Historically, identical Contract IDs could therefore be referenced by Missions
in more than one workspace. A revision command must not turn that legacy shape
into a cross-workspace governance write.

## Decision

Mission Control exposes a revision command anchored to an existing Mission. The
Mission supplies the workspace authorization boundary and Contract lineage ID.
Only human callers with access to that workspace may invoke the command.

The request supplies `expectedVersion`, a complete candidate Contract, and a
non-empty reason. Inside one transaction Mission Control:

1. locks the Mission and Contract lineage;
2. verifies the lineage belongs exclusively to the Mission workspace;
3. reads the latest persisted revision;
4. requires `expectedVersion` to equal the latest version;
5. requires the candidate ID to match and its version to increment by one;
6. inserts the immutable revision and appends `contract.lifecycle.revised`.

The lineage lock is a transaction-scoped PostgreSQL advisory lock derived from
the Contract ID. It closes the phantom-insert race that a row lock on the
current latest revision cannot prevent.

Mission creation uses the same lineage lock and rejects reuse across workspace
boundaries. Historical lineages already shared across workspaces remain readable
but fail closed for new Mission and revision writes.

No existing Mission changes `contractVersion`. This command does not fork,
restart, or rebind a Mission.

## Consequences

Concurrent writers cannot both create the same next version. A stale writer
receives the current version and produces no Contract or event side effect.
Each accepted revision has a human actor, reason, source Mission, parent
version, and new version in the event ledger.

The Mission anchor is a transitional ownership mechanism. A later migration may
materialize workspace ownership on Contract lineages and resolve historical
shared IDs explicitly. Until then, ambiguous ownership is unavailable rather
than guessed.

## Alternatives considered

- Lock only the latest Contract row: rejected because a concurrent insert can
  appear after candidate selection and still race on the next version.
- Let ordinary Mission creation insert later versions: rejected because it has
  no explicit revision intent or concurrency token.
- Automatically rebind the source Mission: rejected because it would change
  active delegated terms and Evidence provenance.
- Treat Contract IDs as globally writable: rejected because global identity is
  not workspace authorization.

## Verification

API tests cover successful revision, stale writers, identity drift, unchanged
Mission binding, human/workspace authorization, and shared-lineage rejection.
Repository tests cover the advisory lock, latest-version query, and workspace
ownership projection. An opt-in PostgreSQL integration test forces two
transactions to contend before the advisory lock and requires exactly one
version 2 row and one revision event.
