# ADR-0073: Materialized Contract Lineage Ownership

> Status: accepted  
> Owner: Mission Control maintainers  
> Date: 2026-08-16  
> Scope: Contract lineage persistence and workspace isolation

## Context

ADR-0072 temporarily derived a Contract lineage's workspace from Missions that
referenced it. That projection prevented new cross-workspace writes, but it made
Mission history carry authorization truth and could not assign ownership to a
lineage before its first Mission insert.

## Decision

Mission Control owns one `mission_contract_lineages` row per Contract ID. The
row binds the lineage to exactly one workspace. Immutable Contract revisions
reference it by Contract ID, and Missions reference the exact Contract/workspace
pair through database foreign keys. Creation serializes the Contract ID, creates
ownership before version 1, and then creates the Mission. Revision compares the
stored workspace with its authorized source Mission.

The migration backfills ownership only when every Mission for a Contract ID is
in one workspace. It aborts when a Contract ID spans workspaces or has no
Mission from which ownership can be proven. Operators must resolve those rows
explicitly before retrying the migration. The migration never selects an
arbitrary workspace or silently renames a Contract ID.

## Consequences

Mission rows are no longer the live Contract authorization source. A Contract
revision cannot exist without a lineage owner, and a lineage cannot change
workspace through Mission creation or Contract revision commands.

Historical shared or orphan lineages make deployment fail closed. This is an
intentional upgrade gate: choosing a workspace or splitting identity changes
governance ancestry and requires an operator-reviewed data migration.

## Alternatives considered

- Add `workspace_id` to every revision: rejected because ownership belongs to
  the lineage and repeated mutable-looking values could drift.
- Continue deriving from Missions: rejected because history is not an
  authorization registry.
- Pick the oldest Mission workspace during migration: rejected because age
  does not establish ownership.

## Verification

Migration tests cover startup ordering, backfill SQL, conflict detection,
downgrade, and head advancement. Opt-in PostgreSQL tests prove successful
backfill and foreign-key enforcement, and prove that shared and orphan lineage
failures roll back under Alembic's migration transaction without leaving a
partial ownership table. API and repository tests prove creation and revision
use the materialized owner.
