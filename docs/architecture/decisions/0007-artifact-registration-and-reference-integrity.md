# ADR-0007: Artifact Registration and Reference Integrity

> Status: accepted  
> Owner: architecture maintainers  
> Date: 2026-08-08  
> Scope: Artifact metadata, WorkUnit completion, Evidence references, and persistence

## Context

Runner completion and verifier Evidence both carry `ArtifactRef` values, but a
digest-shaped reference alone does not prove that the referenced output exists,
belongs to the Mission, or was produced by the current WorkUnit attempt. The
system also needs an auditable metadata record without making Mission Control
responsible for storing artifact bytes.

## Decision

Mission Control owns an immutable Artifact metadata projection. The Runner or
Artifact Store owns the bytes and publishes them at a digest-addressed content
location.

- Registration requires a `RUNNING` Mission, a `RUNNING` WorkUnit, and the
  current unexpired lease fenced by both lease ID and runner ID.
- The record fixes `missionId`, `workUnitId`, `attempt`, kind, digest, content
  address, media type, size, retention, sensitivity, and producer identity.
- The content address must contain the digest. Metadata is immutable: a repeat
  registration with identical values is idempotent; the same ID with different
  values is rejected.
- WorkUnit completion may reference only artifacts registered for that
  WorkUnit and its current attempt. Evidence may reference any registered
  artifact in the same Mission, but its digest must match exactly.
- The registration event is append-only and correlated to the Mission. The
  legacy session/file Artifact API remains a compatibility surface and is not
  business truth for Mission execution.

## Consequences

Completion and verification failures are explicit when an Artifact Store has
not registered metadata, a digest is wrong, or an output belongs to another
attempt. Artifact bytes can move to object storage without changing Mission
contracts, while list and audit queries remain available from Mission Control.
The current metadata projection is intentionally small; retention enforcement,
byte availability checks, and a dedicated Evidence projection are later slices.

## Alternatives considered

- Trust arbitrary digest references: rejected because they permit unverifiable
  or cross-Mission outputs.
- Store artifact bytes in Mission Control: rejected because it couples durable
  workflow state to large, mutable storage concerns.
- Reuse the legacy Artifact API as the Mission source of truth: rejected because
  it models session/file content and has different ownership and lifecycle
  semantics.

## Verification

- Persistence tests cover Artifact add/get/list round trips and bounded queries.
- Migration tests cover fresh installs, every supported prior head, idempotent
  startup, and A2A-to-Artifact upgrade.
- API tests cover registration, idempotency, conflicting metadata, lease and
  content-address rejection, listing, and unregistered completion rejection.

## Supersedes

None.
