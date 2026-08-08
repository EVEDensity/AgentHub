# ADR-0004: Verifier-Gated Mission Success

> Status: accepted  
> Owner: architecture maintainers  
> Date: 2026-08-08  
> Scope: Evidence recording, WorkUnit verification, and Mission terminal state

## Context

Runner completion now records digest-addressed artifact references and moves a
WorkUnit to `VERIFYING`. A terminal success still needs an independent check
against a Contract acceptance criterion. Allowing the Runner or a generic
developer command to submit that check would collapse execution and
verification into the same trust boundary.

## Decision

Mission Control exposes a verifier-only WorkUnit verification command.

- Only `verifier` and `admin` roles may call it; non-admin verifier IDs must
  match the authenticated identity.
- The WorkUnit must be `VERIFYING`, the criterion must belong to the immutable
  Mission Contract, and the Evidence must contain at least one ArtifactRef,
  verifier metadata, a summary, and an integrity hash.
- Evidence is appended to the event ledger as an `evidence` aggregate before
  the WorkUnit verification event.
- `PASS` transitions the WorkUnit to `SUCCEEDED`. Mission Control records
  `RUNNING -> VERIFYING -> SUCCEEDED` only when every WorkUnit is successful
  and every required acceptance criterion has at least one PASS Evidence in
  the mission's event history, all in one transaction.
- `FAIL` transitions the WorkUnit to `FAILED`; `INCONCLUSIVE` records Evidence
  and leaves the WorkUnit in `VERIFYING`.
- The endpoint never stores artifact bytes; Artifact storage remains a
  separate digest-addressed boundary.

## Consequences

Execution and verification have distinct identities and durable event history.
Missions cannot claim success without criterion-scoped Evidence for every
required acceptance criterion. Inconclusive checks require a later verifier
decision, and failed checks remain eligible for a policy-defined retry path.
Event consumers must handle `evidence` aggregates in addition to mission and
work-unit aggregates.

## Alternatives considered

- Let the Runner submit PASS Evidence: rejected because it is not independent.
- Treat any Evidence verdict as Mission success: rejected because all required
  WorkUnits and every required contract criterion must be satisfied.
- Add a separate Evidence table in this slice: deferred until query and
  retention requirements justify a projection beyond the immutable event
  ledger.

## Verification

- API tests cover PASS success, multi-criterion gating, INCONCLUSIVE
  non-success, role enforcement, and verifier identity anti-impersonation.
- The contract criterion and ArtifactRef validators reject malformed Evidence.
- Mission, domain, persistence, and A2A regression suites remain green.

## Supersedes

None.
