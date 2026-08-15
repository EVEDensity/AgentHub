# ADR-0052: A2A Result Bundle Export

> Status: accepted  
> Owner: protocol and Mission Control maintainers  
> Date: 2026-08-15  
> Scope: peer-facing A2A task result reads

## Context

Inbound A2A tasks already map to durable Missions and catalog-bound WorkUnits,
but the peer inbox supported only submit and cancel. A peer could not retrieve
completed Artifact bytes or Evidence. Returning Runner output directly from the
Gateway would bypass Mission success, current-attempt fencing, independent
verification, sensitivity policy, and Artifact integrity checks. Caching a
result in Gateway would also create a second task database.

## Decision

The authenticated peer inbox accepts `tasks/get`. It verifies the source Agent
Card and binds the lookup to `(workspace, canonical source origin, external task
ID)` before calling Mission Control. Gateway performs protocol translation only
and returns the control-plane projection without durable or process-local result
state.

Mission Control returns status only until both the inbound Mission and its
mapped WorkUnit are `SUCCEEDED`. A completed result is an all-or-nothing bundle:

- Evidence must be `PASS`, belong to the mapped WorkUnit, and is exported as a
  typed read-only projection.
- Every exported Artifact must be referenced by that Evidence, belong to the
  same WorkUnit and current attempt, and match the referenced digest.
- Only `public` and `internal` Artifact sensitivity may cross the A2A boundary.
- At most 20 Artifacts, 20 Evidence records, 512 KiB of raw Artifact bytes, and
  900 KiB of encoded bundle JSON are allowed.
- Local or MinIO bytes are reread and checked for registered size and SHA-256 in
  the same operation that produces the exported bytes.
- Content addresses, storage paths, object-store locations, credentials, and
  provider configuration are never returned.

Any missing, corrupt, stale-attempt, digest-mismatched, sensitive, or oversized
member rejects the whole bundle. Partial results are not returned.

This decision covers receiver-side export only. A future sender-side import
must persist bytes into local content-addressed storage, register local Artifact
metadata, and enter local verification. Peer Evidence cannot directly mark the
sender Mission successful.

## Consequences

Peers can retrieve a completed, bounded, integrity-checked result without
learning AgentHub storage topology or bypassing Mission Control. Repeated reads
repeat byte verification, trading storage I/O for a stronger trust boundary.
Large or restricted outputs require a separately governed transfer mechanism;
the A2A task response will fail explicitly instead of truncating data.

Outbound tasks still do not advance from a remote result. End-to-end result
synchronization and third-party A2A conformance remain open release gates.

## Verification

Python tests cover local and MinIO read-and-verify behavior, current-attempt and
digest binding, PASS-only Evidence, sensitivity, raw and encoded size limits,
source isolation, and all-or-nothing error mapping. Go tests cover the control
plane query contract, structured Artifact/Evidence decoding, source-bound inbox
lookup, Authorization forwarding, and peer result serialization.
