# Architecture Documentation

> Status: accepted  
> Owner: architecture maintainers  
> Last reviewed: 2026-08-15
> Scope: stable, reviewable system boundaries

This directory contains architecture that is safe to version and review. It
describes durable boundaries, not confidential product strategy or speculative
feature lists.

## Stable boundaries

- Mission Control owns durable work state and state transitions.
- Runner owns isolated execution and collection of artifacts and evidence.
- Harness owns the bounded model/tool loop inside a WorkUnit execution.
- Context services supply request-scoped context but do not mutate Mission
  state directly.
- A2A exposes or consumes external agent delegation at the system edge.
- MCP exposes tools and resources at the system edge.
- Frontend and protocol adapters are projections; neither is a source of truth.

The detailed target design and migration sequence are stored locally under
`docs/internal/architecture/` because they include private strategic material.

## Contents

- `production-cli-technical-spec.md`: implementation SSOT for the production
  developer CLI, including contracts, boundaries, roadmap, and evidence gates.

- `multi-agent-collaboration.md`: blueprint for the chat-shaped
  multi-agent collaboration system (session event log, @agent trigger,
  receipts retrieval) per ADR-0108.
- `components/system-boundaries.md`: ownership and request flow summary.
- `components/harness.md`: Harness loop, checkpoint, and event boundary.
- `components/runner.md`: execution-plan, polling, backoff, and shutdown boundary.
- `components/a2a-adapter.md`: A2A Agent Card probing and Mission task
  translation boundary.
- `components/mcp-adapter.md`: Stateless MCP client and audit boundary.
- `components/memory.md`: current memory implementation baseline.
- `components/`: stable component-level architecture.
- `decisions/`: accepted and proposed Architecture Decision Records.

The desktop delivery boundary is defined by
`decisions/0103-single-entry-desktop-orchestration.md`: `AgentHub.exe` is the
user entry point, while bundled local services remain supervised implementation
details. This is a target decision until its implementation gates are complete.

Add a new top-level architecture boundary only through an ADR. Component docs
must identify inputs, outputs, owned data, failure behavior, and dependencies.
