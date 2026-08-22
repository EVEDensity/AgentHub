# ADR-0085: Desktop Sidecar Process Supervision

> Status: implemented  
> Owner: desktop maintainers  
> Date: 2026-08-22  
> Scope: `desktop/src-tauri/src/runtime.rs`, `desktop/src-tauri/src/main.rs`

## Context

The desktop must not ask users to run Docker or start the server Runner
manually. The repository's Python Runner is a deployment service and is not a
desktop-bundled executable, so the shell needs an explicit boundary for a
future packaged local Runtime.

## Decision

`LocalRuntime` accepts a launch specification whose production resource path is
the fixed `agenthub-runtime.exe` file under the Tauri resource directory. A
start command first requires the existing secure configuration readiness check,
then verifies that the sidecar is a regular file and launches it with no shell
interpolation. Missing configuration returns `configuration_required`; missing
or failed process launch returns `failed`. A successfully spawned process is
reported as `starting` with `probing` readiness, and stop kills and reaps only
the tracked child.

The sidecar health endpoint is specified separately by ADR-0086. This process
supervisor does not claim readiness from process liveness; it delegates that
decision to the bounded loopback probe.

## Consequences

- Desktop users never need to operate Docker for the local process boundary.
- A build without the packaged sidecar fails explicitly instead of launching a
  mock or server deployment process.
- Runtime configuration remains owned by `ConfigurationStore`; Mission state
  remains outside the desktop.
- A future packaged sidecar can implement the versioned readiness contract
  without changing the launcher ownership boundary.

## Verification

- Native tests cover unconfigured start, missing sidecar failure, spawned child
  tracking, and stop/reap behavior.
- Static checks must pass before packaging work begins.
