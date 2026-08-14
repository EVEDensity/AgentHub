# ADR-0032: Controlled Agent Catalog Writes

> Status: accepted
> Owner: Mission Control maintainers
> Date: 2026-08-14
> Scope: Agent catalog mutation and concurrency

## Context

ADR-0031 added a durable, credential-free Agent catalog projection but no
supported mutation path. Direct SQL administration cannot provide a stable
authorization contract, and a read-then-write update would lose concurrent
changes.

## Decision

Expose a workspace-authorized v1 binding write endpoint. Requests contain only
adapter type, capability snapshot, enabled state, and `expectedVersion`.
Provider credentials, base URLs, and raw configuration are rejected as unknown
fields.

Writes use one database statement with two mutually exclusive branches:

- `expectedVersion = 0` inserts only when the binding does not exist;
- `expectedVersion > 0` updates only when the stored version matches.

Successful writes increment `source_version` exactly once. A missing returned
row is a version conflict and maps to HTTP 409. Database errors and malformed
returned records fail closed as HTTP 503.

## Consequences

Catalog changes are deterministic under concurrent writers and do not copy
provider secrets into Mission Control. Callers must read or retain the last
accepted version before updating. Registry synchronization will use this same
writer contract in a separate change.

## Verification

- Service tests cover create/update versions, conflicts, normalization, and
  malformed database responses.
- API tests cover workspace authorization, secret-field rejection, conflicts,
  and unavailable storage.
- Existing Mission delegation tests continue to resolve bindings read-only.
