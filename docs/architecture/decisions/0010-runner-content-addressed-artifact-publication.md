# ADR-0010: Runner Content-Addressed Artifact Publication

> Status: accepted  
> Owner: architecture maintainers  
> Date: 2026-08-08  
> Scope: Runner artifact publication port and Artifact Store writes

## Context

ADR-0009 made Evidence admission honest by reading and verifying registered
Artifact bytes. The current repository had no reusable Runner-side write path,
so local development still depended on manually pre-seeded files or objects.
Adding a write endpoint to Mission Control would make the control plane own
large bytes and would blur the Runner/Artifact Store boundary.

## Decision

Provide a Runner-facing `ContentAddressedArtifactPublisher` module. It publishes
bytes and returns only `digest`, `sizeBytes`, and `contentAddress`; it never
creates or updates Mission, WorkUnit, or Artifact metadata.

- Local publication stages output, computes SHA-256 and size in bounded chunks,
  and atomically moves it to `<configured-root>/sha256/<digest>`. Existing files
  are re-read and must match before an idempotent response is returned.
- MinIO publication uses the configured bucket and the fixed key
  `artifacts/<digest>`, includes the digest as object metadata, and re-reads an
  existing object before treating it as idempotent. Bucket and address policy
  remain the verifier's responsibility at Evidence admission.
- Publication is bounded by `AGENTHUB_ARTIFACT_PUBLISH_MAX_BYTES`; source,
  storage, and integrity failures are explicit and fail closed.
- The Runner must use the returned values when calling Mission Control's
  lease-fenced Artifact registration operation. Mission Control remains the
  durable metadata and lifecycle owner.

This is a library boundary in the current phase, not a new independently
deployed Runner service. A service boundary will require separate scaling,
security, and failure evidence.

## Consequences

The Community/local profile can produce real digest-addressed bytes without
manual seeding, and the same publication result can flow into registration and
later independent verification. Publication performs a staging copy and may
re-read existing objects, trading I/O for idempotency and integrity. MinIO
object replacement races are not a cryptographic immutability guarantee; the
subsequent verifier remains authoritative at Evidence admission.

## Alternatives considered

- Add multipart upload endpoints to Mission Control: rejected because it makes
  durable workflow state responsible for large byte transport.
- Reuse the document pipeline MinIO client: rejected because it accepts
  arbitrary buckets/keys and does not enforce content-addressed immutability.
- Add a standalone Runner service now: deferred until a real execution loop,
  lease client, isolation profile, and deployment boundary are implemented
  together.

## Verification

- Tests cover local publication, atomic idempotency, corrupt existing bytes,
  source and size failures, MinIO key/metadata publication, existing-object
  verification, and storage failures.
- An end-to-end unit path publishes bytes and feeds the returned address and
  digest into the existing byte verifier.

## Supersedes

None. This decision operationalizes the Runner ownership in the target
architecture and complements ADR-0009.
