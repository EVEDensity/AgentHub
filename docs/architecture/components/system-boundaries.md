# System Boundaries

> Status: accepted  
> Owner: architecture maintainers  
> Last reviewed: 2026-08-09

## Boundary diagram

```text
User / Issue / API
        |
        v
Mission Control
  Mission, Contract, WorkUnit, Events, Policy
        |
        +--> Runner + Harness --> Model Gateway / Tools
        |                          |
        |                          +--> Artifact / Evidence
        |
        +--> Context Compiler --> Memory / RAG / Graph sources
        |
        +--> A2A Adapter (external agents)
        +--> MCP Adapter (tools and resources)
```

## Ownership table

| Component | Owns | Does not own |
|---|---|---|
| Mission Control | durable lifecycle, authorization, budgets, events | model prompts or tool implementation |
| Runner | isolation, worktree, resource limits, artifact upload | business status transitions |
| Harness | model loop, function calling, tool use, checkpoints | durable Mission truth |
| Context Compiler | request-scoped context manifest | WorkUnit status or acceptance |
| Verifier | independent checks and Evidence | the Agent's internal reasoning |
| A2A Adapter | external Agent Card and task translation | internal scheduling and task tables |
| MCP Adapter | tool/resource protocol and request auth | business sessions and Mission state |
| Frontend | user projection and commands | fallback success or durable state |

## Required flow for new work

New work must create a Mission and Contract, derive WorkUnits, acquire a lease,
execute through Harness and Runner, publish immutable Artifacts, run an
independent Verifier, and transition only through Mission Control. A running
WorkUnit may delegate a child only through the ArtifactRef-backed Mission
Control command; the parent lease fences that command, and the child relation
is not a dependency edge. A protocol request is an input to this flow, not an
alternative flow.

## Migration status

LangGraph and AgentNet remain compatibility surfaces while callers migrate.
A2A currently provides a protocol-shaped integration layer and MCP currently
provides a protocol gateway; neither is yet the final stateless or conformance-
verified implementation. Capability claims must be backed by tests and runtime
probes before they are promoted to public product guarantees.
