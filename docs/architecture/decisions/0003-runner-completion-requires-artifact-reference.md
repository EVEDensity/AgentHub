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
the event ledger grows with the immutable reference list. Artifact existence
and Mission/WorkUnit/attempt ownership are established by the Artifact
registration contract in ADR-0007 before completion is accepted.

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
- API tests reject references that have not been registered for the current
  WorkUnit attempt.
- Domain, persistence, and A2A regression suites continue to pass.

## Supersedes

None.
