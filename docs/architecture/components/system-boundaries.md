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

Independent verifier service principals receive the explicit, revocable
`mission:verify` permission through the existing workspace ACL. Mission Control
checks that grant before Artifact reads or Evidence mutation and fails closed
when IAM is unavailable. The authenticated subject must still match the
Evidence verifier identity. The grant is not workspace read access and does not
permit Runner claims or self-verification by an executing Agent.

Verifier discovery is a separate, side-effect-free Mission Control projection.
It returns at most one `VERIFYING` WorkUnit with only the Mission objective,
acceptance criteria, current-attempt Artifact metadata, and bounded WorkUnit
shape needed for evaluation. A short transaction lock keeps the returned
snapshot consistent but is not a verifier lease; `/verify` remains the durable
state-transition authority. Ordinary Mission listing and Artifact bytes are not
part of this projection.

The version-2 discovery projection includes a fail-closed evaluation-policy
decision derived from the Mission Contract. A PASS transition requires one
supported, applicable policy and an exact criterion/configuration-digest match,
checked both before Artifact byte I/O and inside Mission Control's transaction.
Unknown, malformed, ambiguous, unsupported, or unsatisfied policies remain
inconclusive. The initial `artifact-set.v1` evaluator establishes only bounded
Artifact-set presence and byte verifiability; semantic or test correctness
requires a stronger independently implemented evaluator.

Mission Control executes that deterministic structural evaluator against the
exact `ArtifactByteVerification` records produced while the Artifact store is
streamed. The result set must close one-to-one over current-attempt Artifact
IDs, digests, and sizes. The pure evaluation is repeated inside the admission
transaction, while raw Artifact content stays in Runner-owned storage. A
non-throwing storage adapter or caller-supplied verdict is not by itself a PASS
proof.

## Migration status

LangGraph and AgentNet remain compatibility surfaces while callers migrate.
A2A currently provides a protocol-shaped integration layer and MCP currently
provides a protocol gateway; neither is yet the final stateless or conformance-
verified implementation. Capability claims must be backed by tests and runtime
probes before they are promoted to public product guarantees.
