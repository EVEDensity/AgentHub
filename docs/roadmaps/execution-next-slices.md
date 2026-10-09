# Execution follow-up slices

> Status: implemented; local acceptance verified
> Owner: Mission Control maintainers
> Last reviewed: 2026-10-10
> Scope: delivery after deterministic CI and catalog-bound chat admission

The current change establishes the CI gate, additive checkpoint/session storage,
one catalog-bound chat executor, durable session authorization, bounded event
pagination and atomic confirmation. Tests establish local/integration behavior;
these are not claims of distributed or real-provider production readiness.

The four bounded slices below are implemented. Each small step was checked
against its acceptance conditions before integration; independent review found
and corrected receipt, fingerprint, lease, cancellation and UI race boundaries.

1. **Verifier conclusions.** Test/security report evaluation must read the
   registered report's actual conclusion rather than count reports. Acceptance:
   failing findings prevent PASS, malformed/missing reports are inconclusive or
   fail, and an independently authenticated verifier reproduces the result.
   Delivered: strict bounded report schemas, byte-backed conclusion evaluation,
   honest FAIL submissions, and independent server reproduction before Evidence
   is committed. Authenticated SQLite/HTTP tests cover positive and negative
   reports. This evaluates registered reports; it does not rerun their tests or
   scanners. See [ADR-0114](../architecture/decisions/0114-verified-report-conclusions.md).
2. **Crash recovery.** Persist and read the complete bounded resume context with
   workspace/context fingerprints and ToolReceipts. Acceptance: a real process
   kill/restart resumes a safe checkpoint, ambiguous side effects are never
   replayed, and legacy NULL fingerprints remain rejected in strict mode.
   Delivered for the desktop local Runner: private v2 images and real atomic
   result receipts, complete pending-call/iteration cursors, usage and elapsed
   budgets, exact admitted anchors, workspace/model/policy fences and local
   process exclusion. Actual kill/restart tests exercise the production claim
   entrance with real SQLite Mission Control, Harness and CAS, including
   repeated crashes, terminal publication and refused unsafe outcomes. CLI
   preflight is metadata-only and never invents results; changing runtime state
   lives outside the tool workspace with verified legacy copies. Recovery
   requires the same owned live lease/attempt; expired leases and in-flight
   model calls need reconciliation. See [ADR-0115](../architecture/decisions/0115-private-runner-resume-images.md).
3. **Runner availability and chat UX.** Project durable PENDING/RUNNING state and
   matching Runner availability in the frontend. Acceptance: an explicitly
   selected Agent with no Runner visibly waits without reporting execution.
   Extend multiple executors only through explicit WorkUnit dependencies and
   Contract acceptance, retaining Mission Control as the state authority.
   Delivered: authenticated expiring contact observations, exact binding and
   capability matching, read-only durable execution projection and visible
   waiting/verification/recovery states. Admission returns promptly; retries
   remain reads, cancellation is acknowledged by the server, and delayed
   responses cannot alter a new conversation's stream. Frontend unit, type,
   build and browser contract checks cover the no-Runner flow. The single
   catalog executor remains this slice's scope; multiple executors still require
   explicit WorkUnit dependencies and Contract acceptance. No synthetic DAG or
   second business authority was introduced. See
   [ADR-0116](../architecture/decisions/0116-runner-contact-and-chat-execution-status.md).
4. **Historical code debt.** Split the oversized Harness, Runner and database
   modules while retaining contracts. Acceptance: the full audit shrinks and
   each refactor preserves state/lease tests. The PR quality gate prevents growth
   against its merge base without adding static exemptions.
   Delivered: DB DDL/seeds/compatibility migrations, Harness DTO/repair/loop,
   Runner ports/HTTP/compiler/resolver/sync, and CLI resume/state responsibilities
   are separated while preserving compatibility imports. Compared with
   `1584bb7`, the raw audit initially drops oversized modules from 19 to 16 and
   functions above complexity 20 from 94 to 88. One retired exemption was
   removed; none was added. Remaining unrelated historical debt is still visible.

CI configuration and remaining external evidence gaps are governed by
[the CI guide](../development/ci.md). Operational schema checks are in
[the migration runbook](../operations/checkpoint-resume-migration.md).
