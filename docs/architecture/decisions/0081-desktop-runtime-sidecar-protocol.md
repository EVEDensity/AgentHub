# ADR-0081: Versioned Desktop Runtime Sidecar Protocol

> Status: accepted
> Owner: desktop maintainers
> Date: 2026-08-22
> Scope: `desktop/src-tauri/`, local Runtime lifecycle

## Decision

The desktop shell and its future bundled Runtime communicate through a small,
versioned lifecycle contract. A `RuntimeSnapshot` reports protocol version,
process status, readiness, process id, exit code, and a redacted detail string.
Process liveness and service readiness are separate fields. The desktop must
not treat an alive process as proof that the Runtime can accept work.

The protocol is limited to local process supervision. Mission, Contract,
WorkUnit, Artifact, Evidence, Decision, and Outcome remain owned by Mission
Control and are not mirrored in the sidecar protocol.

## Consequences

- The desktop can show actionable startup and crash states without fabricating
  business success.
- A future sidecar can implement health/readiness probing without changing the
  desktop's process boundary.
- Protocol versioning is explicit before a bundled process is introduced.

## Verification

- Native tests cover unconfigured start, active-process probing, and exit-code
  reporting.
- The UI renders lifecycle and readiness independently.
