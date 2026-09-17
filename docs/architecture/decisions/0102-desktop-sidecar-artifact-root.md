# ADR-0102: Desktop Sidecar Local Artifact Root

> Status: implemented
> Owner: desktop maintainers
> Date: 2026-08-24
> Scope: `desktop/runtime-sidecar/`, `desktop/src-tauri/src/runtime.rs`

## Context

The desktop shell stores an absolute local Artifact directory in configuration.
ADR-0087 introduced a bootstrap sidecar with loopback readiness only. Without
a local consumption boundary, the directory remained a desktop setting that the
Runtime process could not verify or expose to future local execution.

The sidecar must not call Mission Control, hold control-plane tokens, or mirror
Mission state. It may only validate and advertise a host-local filesystem path
for future Runner-adjacent capabilities.

## Decision

When the desktop starts the packaged sidecar, it passes the configured Artifact
directory through a fixed launch argument `--artifact-root <absolute-path>`.
Renderer input cannot choose the argument name or inject additional flags.

The sidecar validates the path at startup:

- The path must be absolute.
- The directory is created when missing.
- The directory must be writable.
- Validation failures are reported through `artifactRootStatus` on `GET /readyz`
  and never crash the process.

The readiness JSON remains protocol version `1` and extends the contract with an
optional field:

```json
{"protocolVersion":1,"status":"ready","artifactRootStatus":"ready"}
```

`artifactRootStatus` values are `not_configured`, `ready`, and `unavailable`.
When the desktop supplied `--artifact-root`, it treats readiness as healthy only
when `artifactRootStatus` is `ready`. The response does not include the path,
Mission identifiers, or file listings.

## Consequences

- Local Artifact configuration becomes an executable contract instead of UI-only
  metadata.
- Future local execution can depend on a verified CAS root without moving
  Mission truth into the desktop shell.
- Packaging smoke must cover the artifact-root launch path when enabled.
- The sidecar still does not publish or verify remote Artifact bytes.

## Verification

- Sidecar unit tests cover argument parsing, directory creation, and readiness
  JSON for ready and unavailable roots.
- Desktop native tests cover launch arguments and readiness rejection when the
  artifact root is unavailable.
- Existing loopback readiness tests remain valid when no artifact root is
  supplied.
