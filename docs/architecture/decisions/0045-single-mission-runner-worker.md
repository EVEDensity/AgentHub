# ADR-0045: Single-Mission Runner Worker Supervision

> Status: accepted  
> Owner: Runner and Mission Control maintainers  
> Date: 2026-08-15  
> Scope: polling ownership, readiness, backoff, and shutdown semantics

## Context

ADR-0044 provides a trustworthy request-scoped execution plan, but the
repository has no process supervisor for repeated claims. `claim_and_run`
currently requires a Mission ID; no Mission Control API yet exposes a global
ready-work queue. Inventing a local queue or scanning legacy task tables would
create another scheduler and business truth.

The process must also distinguish liveness from readiness, avoid hot-looping an
empty or unavailable control plane, preserve active lease cancellation
semantics, and avoid exposing exception content through health state.

## Decision

Introduce `RunnerWorker` as a process-local supervisor for one explicitly
configured Mission ID. It repeatedly calls the existing bound
`claim_and_run`, passing a fixed lease duration. It persists no work state and
does not discover Missions.

An empty successful claim marks the control path ready and increases delay
exponentially up to a configured maximum. A claimed WorkUnit resets delay to
the minimum. A non-cancellation exception marks the worker unready, records only
its type and counters, and uses the same bounded backoff. `CancelledError`
always propagates.

Graceful stop prevents a new poll and waits for the active claim. A process
adapter may cancel after its own shutdown deadline; cancellation then reaches
`WorkUnitRunner`, which owns lease-fenced failure reporting. The public worker
snapshot is in-memory operational metadata, not Mission state.

## Consequences

The claim loop can be tested and embedded without introducing NATS, Redis, a
new database, or a legacy task dependency. Health adapters can report running
and ready separately without inspecting Mission content.

Capacity is intentionally limited to explicitly assigned Missions. A scalable
Runner pool requires a later Mission Control ready-work discovery contract with
workspace authorization, binding filters, fair ordering, and atomic claim. The
worker must switch to that contract rather than grow its own queue.

## Verification

Tests cover idle and error backoff, reset after a claim, fixed Mission and lease
arguments, sanitized failure snapshots, stop during active work, cancellation
propagation, duplicate run rejection, and invalid configuration.
