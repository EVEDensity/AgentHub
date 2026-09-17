# AgentHub Repository Guide

This file is the entry point for AI coding agents and human reviewers. Read it
before changing code or documentation.

## Required reading order

1. `docs/README.md` - documentation map and source-of-truth rules.
2. `docs/architecture/README.md` - stable architecture boundaries.
3. `docs/internal/README.md` - local private plans, when the directory exists.
4. The nearest `README.md` in the area being changed.
5. Tests and contracts for that area before implementation.

## CodeGraph

When `.codegraph/` exists, use `codegraph_explore` before grep, find, or broad
file reads to locate symbols and understand call paths. The index is local and
must not be committed.

## Repository boundaries

- `app/`: Python control plane and legacy application integration.
- `services/`: independently runnable Go, Python, and Rust services.
- `frontend/`: Next.js user interface; it does not own business truth.
- `platform/`: versioned cross-process contracts and platform metadata.
- `plugins/`: extension packages and plugin documentation.
- `deploy/`: reproducible deployment assets.
- `tests/`: cross-cutting contract, domain, API, and persistence tests.
- `docs/`: governed public documentation and ignored private design material.

Do not add new top-level directories without updating `docs/README.md` and
recording the reason in an ADR when the boundary is architectural.

## Architectural direction

New execution behavior must converge on Mission, Contract, WorkUnit, Artifact,
Evidence, Decision, and Outcome. Legacy Task, DAG, AgentNet, A2A, and MCP types
must not become additional sources of business truth.

- A2A is an external-agent protocol adapter.
- MCP is a tool and resource protocol adapter.
- Harness owns model loops, function calling, tool use, checkpoints, and budget.
- Mission Control owns durable state and transitions.
- Runner owns isolated execution and evidence collection.

Detailed target architecture is local at
`docs/internal/architecture/target-architecture.md` and intentionally ignored.

## Change rules

- Preserve existing user changes in a dirty worktree.
- Prefer the smallest vertical slice that produces a real, verifiable outcome.
- Do not return demo or synthetic success from production API paths.
- State transitions must be explicit, transactional, and covered by tests.
- Executing agents cannot be the sole verifier of their own output.
- Add or update the nearest README when a module's responsibility changes.
- Add an ADR for changes to ownership, protocols, persistence, or deployment.

## Verification

Run tests in proportion to the change. At minimum, verify the changed module,
its contracts, and the state transitions or user flow it affects. Record test
gaps explicitly in the handoff.
