# ADR-0055: Narrow Verifier Work Discovery

> Status: accepted  
> Owner: Mission Control and verification maintainers  
> Date: 2026-08-16  
> Scope: verifier work discovery and verification-context projection

## Context

ADR-0054 authorized distinct verifier service principals to submit Evidence,
but a verifier still had no controlled way to discover eligible work. Granting
the ordinary Mission, WorkUnit, Contract, and Artifact list APIs would expose a
workspace-wide read surface unrelated to verification. Reusing the Runner lease
would also conflate execution ownership with independent evaluation.

## Decision

Mission Control exposes the additive
`POST /api/v1/missions/verification-work-items/discover` command. The
authenticated principal must be a verifier and must hold `mission:verify` for
the requested workspace before Mission data is read. Administrator access and
the temporary subject-equals-workspace compatibility rule remain unchanged.

The repository selects one deterministically ordered `VERIFYING` WorkUnit whose
Mission is `RUNNING` or `VERIFYING`. Never-evaluated units sort before units with
Evidence; previously inconclusive units then sort by their oldest last Evidence
time. This prevents one repeatedly inconclusive unit from starving untouched
work without introducing a scheduling cursor. Mission and WorkUnit rows are
locked only for the short context-read transaction. Discovery creates no lease,
event, cursor, Evidence, or other durable claim. The existing verification
command's transaction and state checks remain the authority when concurrent
verifiers attempt to record a result.

The version-1 response is a narrow projection containing:

- Mission ID, title, and objective;
- Contract ID, version, and acceptance criteria;
- WorkUnit ID, kind, input ArtifactRefs, expected outputs, status, and attempt;
- at most 200 exact-current-attempt Artifact metadata projections.

It excludes Mission source and creator data, repository scopes, capability
grants, Runner assignment and lease data, Artifact creator data, credentials,
and Artifact bytes. Missing current-attempt Artifacts, an absent Contract,
invalid ownership, and oversized results fail closed instead of returning a
partial context.

The public response schema is additive and versioned at
`platform/contracts/v1/verification-work-discovery-response.schema.json`.
Mission Control is the producer; the future verifier process is the consumer.
Existing Mission and Runner clients require no migration. Rollback removes the
new route and schema consumer while leaving WorkUnit state and Evidence intact.

## Consequences

A verifier can poll for necessary work without receiving general Mission list
authority. The response is replayable metadata, but it is not a durable work
assignment; duplicate evaluation remains possible and safe admission is
serialized by Mission Control.

This decision does not define evaluation policy, Artifact byte transport,
integrity-hash computation, automatic verdicts, verifier heartbeat, or process
recovery. Those remain separate fail-closed slices.

## Verification

Repository tests cover workspace and lifecycle filters, deterministic ordering,
short transaction locking, and attempt-scoped Artifact reads. API tests cover
authorization before Mission reads, idle discovery, minimal projection, exact
attempt selection, no Mission listing, and failure without current Artifacts.
The versioned contract tests validate ready/idle payload consistency.
