# Execution follow-up slices

> Status: target
> Owner: Mission Control maintainers
> Last reviewed: 2026-10-09
> Scope: delivery after deterministic CI and catalog-bound chat admission

The current change establishes the CI gate, additive checkpoint/session storage,
one catalog-bound chat executor, durable session authorization, bounded event
pagination and atomic confirmation. Tests establish local/integration behavior;
these are not claims of distributed or real-provider production readiness.

Proceed in this order, retaining one verifiable vertical slice per change:

1. **Verifier conclusions.** Test/security report evaluation must read the
   registered report's actual conclusion rather than count reports. Acceptance:
   failing findings prevent PASS, malformed/missing reports are inconclusive or
   fail, and an independently authenticated verifier reproduces the result.
2. **Crash recovery.** Persist and read the complete bounded resume context with
   workspace/context fingerprints and ToolReceipts. Acceptance: a real process
   kill/restart resumes a safe checkpoint, ambiguous side effects are never
   replayed, and legacy NULL fingerprints remain rejected in strict mode.
3. **Runner availability and chat UX.** Project durable PENDING/RUNNING state and
   matching Runner availability in the frontend. Acceptance: an explicitly
   selected Agent with no Runner visibly waits without reporting execution.
   Extend multiple executors only through explicit WorkUnit dependencies and
   Contract acceptance, retaining Mission Control as the state authority.
4. **Historical code debt.** Split the oversized Harness, Runner and database
   modules while retaining contracts. Acceptance: the full audit shrinks and
   each refactor preserves state/lease tests. The PR quality gate prevents growth
   against its merge base without adding static exemptions.

CI configuration and remaining external evidence gaps are governed by
[the CI guide](../development/ci.md). Operational schema checks are in
[the migration runbook](../operations/checkpoint-resume-migration.md).
