# AgentHub Documentation System

`docs/` is managed as documentation-as-code. Documents are organized by
audience, authority, and lifecycle instead of by the date or person that
created them.

## Start here

| Need | Location | Version controlled |
|---|---|---|
| Public product and user documentation | `docs/index.md`, `docs/zh/` | Yes |
| Stable architecture and ADRs | `docs/architecture/` | Yes |
| Developer guidance | `docs/development/` | Yes |
| Operations and migration runbooks | `docs/operations/` | Yes |
| Public delivery roadmaps | `docs/roadmaps/` | Yes |
| Documentation governance | `docs/governance/` | Yes |
| Product strategy and detailed internal design | `docs/internal/` | No |
| Generated reports and diagrams | `docs/generated/` | No |

The private `docs/internal/` tree is ignored by Git. It may contain commercial
strategy, competitive analysis, detailed architecture drafts, review records,
and migration working notes. It is available to local AI agents but is not part
of a clone or release artifact.

## Sources of truth

Use this precedence when documents disagree:

1. Versioned contracts and executable tests.
2. Accepted Architecture Decision Records.
3. Current implementation and database migrations.
4. Stable architecture documentation.
5. Active internal roadmap.
6. Historical or archived documents.

A roadmap is intent, not proof of implementation. Public capability claims
must be backed by code and tests and must not be inferred from a plan.

## Document states

Every architecture, roadmap, or operational document must declare one state:

- `draft`: under discussion and not authoritative.
- `accepted`: approved direction or decision.
- `implemented`: verified against the current system.
- `production-verified`: implemented plus recorded real-environment evidence.
- `target`: planned or incomplete; not an implementation claim.
- `superseded`: replaced, with a link to the replacement.
- `archived`: retained only for historical context.

See `docs/governance/documentation-standard.md` for ownership, naming, and
review requirements.

## Repository-level maps

The root `AGENTS.md` is the AI and reviewer entry point. Major implementation
areas also contain a local `README.md` describing ownership and placement:

- `app/README.md`
- `desktop/README.md`
- `services/README.md`
- `frontend/README.md`
- `platform/README.md`
- `deploy/README.md`
- `tests/README.md`
