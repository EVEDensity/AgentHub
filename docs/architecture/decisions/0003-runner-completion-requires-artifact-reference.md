# ADR-0003: Runner Completion Requires Artifact Reference

> Status: accepted  
> Owner: architecture maintainers  
> Date: 2026-08-08  
> Scope: WorkUnit completion API, execution events, and verification boundary

## Context

The Runner completion endpoint previously moved a leased WorkUnit into
`VERIFYING` without recording which immutable output should be checked. That
made verification non-replayable and allowed a completion request with no
inspectable result.

## Decision

The completion command must include at least one `ArtifactRef` containing an
artifact identifier and a digest. Mission Control records those references in
the `work_unit.lifecycle.completed` event and transitions the WorkUnit only to
`VERIFYING`.

Artifact bytes and independent verifier records remain separate concerns. A
Runner cannot use this command to transition a WorkUnit to `SUCCEEDED`; a later
Verifier operation must evaluate the referenced artifacts and provide Evidence.

## Consequences

Every verification attempt has a durable pointer to the output it claims to
produce. Existing clients must add `artifactRefs` to completion requests, and
the event ledger grows with the immutable reference list. The system still does
not claim artifact existence or verifier approval until those follow-up
operations are implemented.

## Alternatives considered

- Accept completion without output references: rejected because verification
  cannot identify the result.
- Mark `SUCCEEDED` from the Runner response: rejected because the Runner is not
  an independent verifier.
- Store only an opaque completion message: rejected because it is not
  content-addressed or replayable.

## Verification

- API tests reject empty `artifactRefs` without changing WorkUnit state.
- API tests assert valid digest references are persisted in the completion
  event and the resulting status is `VERIFYING`.
- Domain, persistence, and A2A regression suites continue to pass.

## Supersedes

None.
