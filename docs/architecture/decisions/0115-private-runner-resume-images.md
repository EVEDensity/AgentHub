# ADR-0115: Private Runner resume images

> Status: accepted
> Owner: execution maintainers
> Date: 2026-10-09

## Context

Public checkpoint counters and metadata-only successful receipts cannot rebuild
a model turn. Resetting budgets, replacing results with a success placeholder,
or rerunning an uncertain side effect would create an unsupported outcome.

## Decision

Mission Control continues to own lifecycle, lease, attempt and contiguous
checkpoint admission. Protocol v2 adds `nextAction.resumeImageDigest`; public
checkpoints contain only the SHA-256 anchor and existing safe metadata.
Runner owns a private SQLite resume image, limited to 2 MiB without truncation.
It includes compiled request and credential-free model context, tool schemas,
permission and budget policy, completed results, the remaining ordered tool
calls, reserved-call identity, usage, elapsed execution time and final text.
The original absolute timeout deadline is retained across process restarts;
fingerprint overhead and downtime cannot replenish the execution time budget.
Provider API keys are excluded; the configured endpoint is represented by a
digest. Changing model, context, grants, budgets or workspace refuses recovery.

For the production guidance wrapper, the private image also binds the exact
Mission/WorkUnit/attempt, visited mission-ledger sequence and event identities,
and actual injected guidance blocks. A saved cursor is restored into the shared
worker consumption ledger; subsequent model rounds read only later events and
do not reinject old blocks. The normal shared-worker once-only behavior remains.
Strict guidance reads fail before model invocation on malformed/unavailable
pages or configured state limits; compatibility callers retain best-effort
guidance. The guidance protocol and limits participate in the context digest.
Images from a guided execution that lack this complete state are refused.

The image is saved before public admission. Recovery loads the exact admitted
checkpoint ID and digest, never a newer unadmitted candidate. Old images are
pruned only after the full returned metadata is independently checked against
the submitted checkpoint. Checkpoint numbering continues from that anchor.

SQLite ToolReceipts atomically claim STARTED and persist a terminal outcome
with its real canonical result, capped at 1 MiB, digest and post-execution
workspace revision. A successful receipt can close the gap between a tool
finishing and TOOL_COMPLETED admission. Its actual result and execution time
are restored; that tool is not executed again. STARTED, UNKNOWN, FAILED,
missing successful bodies and corrupt results refuse automatic recovery.
In-flight model requests refuse recovery because their usage/outcome is unknown.
Legacy v1/NULL checkpoints remain diagnostic records and cannot rebuild a turn.

Model-visible tool feedback has a request-scoped character policy included in
the context fingerprint. Normal execution and receipt reconciliation use the
same pure limiter, deriving consumption from already saved visible results.
The durable desktop Harness retains the gateway's permission/hook services but
does not consume the process-global ResultStorage counter. Raw receipts stay
complete and bounded; restart neither replenishes nor changes feedback limits.

The desktop factory binds this journal to its actual tool workspace and model
manifest. Resume runs only behind the same still-valid owned lease and attempt;
expired or changed leases require explicit reconciliation. A local OS-owned
attempt lock stops simultaneous workers from taking over a live execution and
is released automatically on process death. This supplements server fencing;
it is not a distributed ownership protocol.
Runner retains that lock through Artifact publication, registration and durable
completion, closing it on every exit. Standalone Harness callers still release
their own lock when execution finishes.

Private state lives outside the writable workspace, under
`AGENTHUB_RUNNER_STATE_ROOT` or the user's `.agenthub-runner-state`, partitioned
by resolved workspace identity. CLI control database, artifacts and changing
runtime files also move outside the model workspace. Existing local control
state is retained at its source and copied with SQLite backup/integrity checks
and byte verification; conflicting destinations refuse migration. User facts,
configuration and conversation remain workspace-local. Workspace fingerprints
cover actual bytes, including Git-ignored files; runtime state is not exempted
merely because it is ignored by Git.

## Verification and limits

`tests/integration/test_recovery_process.py` kills actual Python child processes
using real SQLite Mission Control, Runner, Harness, receipts and CAS. It checks
both safe boundaries, the completed receipt gap, remaining calls and budgets,
terminal publication, active-worker exclusion, workspace drift and uncertain
model/tool outcomes. Independent contract/API/database tests check public
admission and lease fencing. These deterministic process tests do not establish
real-provider billing reconciliation or recovery across hosts. Factories without
a complete model manifest/private journal fail closed on resume.
