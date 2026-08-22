# ADR-0090: Desktop Packaged Runtime Smoke

> Status: accepted  
> Owner: desktop maintainers  
> Date: 2026-08-22  
> Scope: `desktop/packaged-runtime-smoke.ps1`, packaged sidecar output

## Context

Artifact hashes prove that the expected sidecar bytes were copied into the
Tauri release directory, but they do not prove that the exact packaged
executable can bind its loopback endpoint and serve the runtime contract.

## Decision

Add a bounded Windows smoke that starts only the packaged
`agenthub-runtime.exe`, polls `http://127.0.0.1:18097/readyz`, requires HTTP
success with protocol version `1` and `ready`, and terminates the process it
started. The smoke never starts the desktop GUI, Mission Control, Runner,
Docker, or a model provider.

## Consequences

- The release directory is tested as an executable boundary, not only as a
  collection of files.
- Port conflicts, early exits, malformed responses, and readiness timeouts
  fail the packaging gate explicitly.
- GUI and installer validation remain separate gates.

## Verification

- The smoke passes against the current Tauri release directory.
- Full MSI/NSIS verification remains dependent on installer backend permissions.
