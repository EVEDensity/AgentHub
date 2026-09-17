# ADR-0035: Bind Evidence Artifacts to the Verified WorkUnit Attempt

> Status: accepted  
> Owner: Mission Control maintainers  
> Date: 2026-08-15  
> Scope: Evidence admission, delegated WorkUnit verification and recovery

## Context

Artifact registration and WorkUnit completion already require an Artifact to
belong to the current WorkUnit and attempt. Evidence admission previously
validated only Mission ownership, so a verifier could submit a sibling
WorkUnit's ArtifactRef. This is especially dangerous for delegated children:
the child could be marked successful using an unrelated result, while lease
recovery and retry logic correctly operated on a different attempt.

## Decision

Evidence admission resolves the target WorkUnit before byte verification and
requires it to be `VERIFYING`. ArtifactRefs must belong to that WorkUnit. When
the WorkUnit has a positive attempt, ArtifactRefs must also match that attempt.
After byte verification, Mission Control repeats the same WorkUnit and attempt
checks under the Mission and WorkUnit locks before persisting Evidence or
changing lifecycle state. A zero-attempt `VERIFYING` snapshot is accepted only
for compatibility with pre-lease fixtures; real execution always reaches
`VERIFYING` with a positive attempt.

## Consequences

Delegated outputs cannot borrow evidence from another branch of the execution
graph. Artifact bytes remain outside Mission Control, but their immutable
metadata, WorkUnit, attempt, verifier, and Evidence are causally connected.
Lease expiry can safely move a delegated unit to `RETRYING`; artifacts from the
expired attempt cannot satisfy Evidence for the next attempt.

## Alternatives considered

- Mission-only Artifact validation was rejected because it permits cross-unit
  evidence substitution.
- Deleting artifacts when a lease expires was rejected because artifacts are
  immutable audit material and may be needed to diagnose the failed attempt.
- Trusting the verifier's WorkUnit or attempt claims was rejected because the
  verifier is not the source of durable execution state.

## Verification

- API tests reject a sibling WorkUnit's ArtifactRef without invoking the byte
  verifier.
- Delegated lifecycle tests cover Artifact registration, completion, Evidence,
  Mission success, lease recovery, and re-claim with a new attempt.

## Supersedes

None.
