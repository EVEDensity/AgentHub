# Documentation Governance Standard

> Status: accepted  
> Owner: repository maintainers  
> Review cycle: quarterly and on architectural boundary changes

## Purpose

This standard keeps implementation, architecture, plans, and historical notes
separable. It is designed for human review and deterministic AI navigation.

## Placement rules

| Document type | Required location |
|---|---|
| System context, containers, components, data ownership | `docs/architecture/` |
| Architecture decision | `docs/architecture/decisions/` |
| Coding, extension, and contribution guide | `docs/development/` |
| Deployment, incident, migration, and recovery runbook | `docs/operations/` |
| Committed delivery roadmap without confidential detail | `docs/roadmaps/` |
| User-facing guide and API documentation | `docs/zh/` or a locale peer |
| Strategy, competition, pricing, private target design | `docs/internal/` |
| Obsolete but historically useful material | `docs/internal/archive/` |
| Generated graph, report, or export | `docs/generated/` |

Do not add architecture or planning Markdown files to the repository root.

## Required metadata

Architecture, ADR, roadmap, and runbook documents must state:

- status;
- owner;
- last reviewed date;
- scope;
- replacement document when superseded.

Use ISO dates (`YYYY-MM-DD`). Avoid version numbers in filenames unless the
document describes a versioned external contract.

Capability claims also follow [Documentation Status and Evidence](../development/documentation-status.md).

## Review requirements

- Architecture changes require an ADR before or with implementation.
- Public feature claims require a link to tests or an implementation location.
- Runbooks must identify prerequisites, rollback, and verification.
- Roadmaps must define acceptance criteria and explicit stop conditions.
- Diagrams must identify state ownership and trust boundaries.
- Private documents must not be linked from public navigation or release docs.

## AI reading contract

AI agents start at `AGENTS.md`, then this documentation map, then the nearest
module README. Plans must never override current tests or contracts. When a
private internal tree is absent, the agent must derive current behavior from
CodeGraph, code, migrations, and tests rather than inventing the missing plan.

## Lifecycle

When replacing a document, mark the old document `superseded` or move it to
`docs/internal/archive/`. Do not silently maintain two active descriptions of
the same architecture. Review stale capability claims during every release.
