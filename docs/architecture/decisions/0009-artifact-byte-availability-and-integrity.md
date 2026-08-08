# ADR-0009: Artifact Byte Availability and Integrity Verification

> Status: accepted  
> Owner: architecture maintainers  
> Date: 2026-08-08  
> Scope: Artifact storage port, Evidence admission, and Mission verification API

## Context

Artifact registration proves that Mission Control has immutable metadata, but
it does not prove that the external bytes still exist or match the registered
size and SHA-256 digest. Evidence based only on metadata can therefore claim a
result whose output is missing, truncated, or corrupt. Mission Control must
check the bytes without taking ownership of them or holding database locks
during object-storage I/O.

## Decision

Evidence admission requires independent Artifact byte verification through a
read-only storage port before any Evidence, event, WorkUnit, or Mission write.

- `local:sha256/<hex>` resolves only to `<configured-root>/sha256/<hex>`.
  Resolution is confined to the configured root, including resolved symlinks.
- `minio://<bucket>/<key>` is accepted only for the configured bucket. The
  registered digest must be a complete key segment; parent traversal,
  credentials, query parameters, and fragments are rejected.
- Arbitrary HTTP URLs and every other content-address scheme fail closed.
- Content is streamed in bounded chunks. Both byte count and SHA-256 are
  compared with immutable Artifact metadata, and a configured maximum limits
  the amount accepted for verification.
- Storage access occurs before the state-mutation transaction. The transaction
  then revalidates Mission and WorkUnit lifecycle state, Artifact references,
  and the complete Artifact metadata observed before storage access.
- Unavailable or unsupported bytes return HTTP 424. Size, digest, address, or
  metadata integrity failures return HTTP 409.

Runner and Artifact Store continue to own byte creation and retention. Mission
Control owns metadata and the decision to admit Evidence.

## Consequences

A verifier cannot record Evidence for missing or corrupt registered output, and
slow storage does not extend Mission or WorkUnit lock duration. Deployments must
mount the configured local content-addressed root or provide MinIO credentials
and bucket access. Verification adds one full streaming read per referenced
Artifact and is intentionally sequential in this first slice to bound resource
use.

This check proves availability and integrity at verification time only. It does
not cryptographically enforce object immutability after verification, validate
the caller-supplied Evidence `integrityHash`, or make the executing Runner an
independent verifier.

## Alternatives considered

- Trust registered metadata: rejected because metadata cannot prove current
  byte availability or integrity.
- Read bytes inside the database transaction: rejected because storage latency
  would hold lifecycle locks and reduce control-plane availability.
- Fetch arbitrary HTTPS content addresses: rejected because it expands SSRF,
  redirect, credential, and network-policy risk.
- Store bytes in Mission Control: rejected because it couples workflow truth to
  large-object storage and retention.

## Verification

- Storage tests cover valid and missing local bytes, size and digest mismatch,
  configured limits, unsupported schemes, MinIO bucket/key confinement,
  missing and corrupt objects, streaming, and response cleanup.
- API tests prove storage I/O occurs outside the transaction, metadata is
  revalidated afterward, and HTTP 424/409 failures leave Evidence, events,
  WorkUnit, and Mission state unchanged.

## Supersedes

None. This decision implements the byte-availability deferral in ADR-0007 and
extends the Evidence admission boundary in ADR-0008.
