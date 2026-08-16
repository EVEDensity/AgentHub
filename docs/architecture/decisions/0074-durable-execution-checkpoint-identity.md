# ADR-0074: Durable Execution Checkpoint Identity

> Status: accepted  
> Owner: Mission Control and Harness maintainers  
> Date: 2026-08-16  
> Scope: checkpoint identity, persistence, and future Mission derivation

## Context

Harness emits request-scoped checkpoints containing loop counters, usage, and
tool results. The in-memory port supports supervision but cannot anchor durable
ancestry. Persisting it verbatim would store unbounded model and tool content,
could capture secrets, and would falsely imply restart-safe model-loop resume.

## Decision

Mission Control owns a separate content-minimized `ExecutionCheckpoint`.
Runners submit checkpoints only while holding the active lease for the exact
WorkUnit attempt. IDs are idempotent, sequences are contiguous within an
attempt, the first phase is execution start, and no record may follow a terminal
phase. Mission Control generates a SHA-256 digest over canonical checkpoint
metadata and appends `work_unit.checkpoint.recorded` in the same transaction.

The durable record contains identity, phase, counters, usage, terminal state,
and a bounded failure reason. It contains no prompts, model responses, function
arguments, tool results, credentials, Artifact bytes, or arbitrary metadata.

A checkpoint is an ancestry anchor, not resumable execution state. A future
Mission fork may reference only a terminal successful checkpoint and must carry
actual reusable inputs through validated Artifact references. The fork creates
a new Mission and never rebinds the source Mission.

## Consequences

Checkpoint identity survives Runner restart and can be audited without making
Harness a database owner. Lease loss, attempt drift, sequence gaps, conflicting
idempotency keys, and writes after terminal state fail without side effects.

The production Harness is not connected to this command in this slice. A
follow-up adapter must map request-scoped Harness checkpoints to this bounded
contract and submit them through Mission Control under the Runner lease.

## Alternatives considered

- Persist `HarnessCheckpoint` directly: rejected because `tool_results` carry
  content and the type is intentionally request-scoped.
- Store checkpoints as generic events only: rejected because fork ancestry
  needs indexed identity and attempt-scoped uniqueness.
- Treat a checkpoint as a full resume image: rejected until model, tool, and
  sandbox state have an explicit portable replay contract.

## Verification

Domain tests enforce terminal shape. API tests cover lease fencing, idempotency,
contiguous ordering, terminal closure, and server-generated digests. Repository
and migration tests cover round trips, latest-sequence lookup, uniqueness,
bounded phases, and the startup migration chain.
