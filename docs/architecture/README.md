# Architecture Documentation

> Status: accepted  
> Owner: architecture maintainers  
> Last reviewed: 2026-08-08  
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

- `components/system-boundaries.md`: ownership and request flow summary.
- `components/memory.md`: current memory implementation baseline.
- `components/`: stable component-level architecture.
- `decisions/`: accepted and proposed Architecture Decision Records.

Add a new top-level architecture boundary only through an ADR. Component docs
must identify inputs, outputs, owned data, failure behavior, and dependencies.
