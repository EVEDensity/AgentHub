# ADR-0088: Desktop Windows Packaging Gate

> Status: accepted  
> Owner: desktop maintainers  
> Date: 2026-08-22  
> Scope: `desktop/package-windows.ps1`, `desktop/runtime-sidecar/`

## Context

The desktop shell now supervises a real target-specific sidecar, but a
developer can still accidentally invoke Tauri with a missing or mismatched
external binary. A packaging failure must be explicit and must not be hidden
behind a mock runtime or a server-side Docker dependency.

## Decision

Provide one Windows packaging command that resolves the pinned Rust host
target, builds and stages `agenthub-runtime` for that target, runs the
configuration and staged-file preflight, verifies the optional developer Tauri
CLI, and only then invokes `cargo tauri build`. The command fails closed at
each boundary and never starts Mission Control, Runner, Docker, or another
server dependency.

The Tauri CLI remains a build-time tool. It is not bundled into the product and
is not required for the installed desktop application to supervise its local
sidecar.

## Consequences

- CI and maintainers have one repeatable Windows packaging entry point.
- A target mismatch or missing sidecar is reported before Tauri starts.
- A missing CLI is an actionable setup error rather than a false package
  success.
- Full bundle verification still depends on a machine with the pinned Rust
  toolchain and Tauri CLI installed.

## Verification

- `runtime-sidecar/packaging-preflight.ps1` passes for the staged host target.
- Sidecar and Tauri native test suites remain green.
- The complete bundle command is intentionally not claimed as verified when
  the Tauri CLI is unavailable.
