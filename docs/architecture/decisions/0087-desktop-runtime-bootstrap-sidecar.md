# ADR-0087: Desktop Runtime Bootstrap Sidecar

> Status: implemented  
> Owner: desktop maintainers  
> Date: 2026-08-22  
> Scope: `desktop/runtime-sidecar/`, Tauri bundle configuration

## Context

The desktop needs a process it can own without requiring users to start Docker.
The server-side Python Runner is intentionally dependent on Mission Control,
an AI Gateway, MCP, mounted credentials, and shared Artifact storage. Bundling
that service into the desktop would hide deployment requirements and create a
second installation topology.

## Decision

Add a standalone Rust `agenthub-runtime` bootstrap sidecar under
`desktop/runtime-sidecar/`. It binds only loopback `127.0.0.1:18097`, serves the
versioned `GET /readyz` contract from ADR-0086, bounds request size and socket
timeouts, and rejects unsupported endpoint arguments. Tauri declares the
sidecar as an `externalBin` resource for platform packaging.

This process currently owns lifecycle and readiness only. It does not claim
Mission work, call models, expose MCP, or report business success. The Python
Runner remains a server/deployment component until a separate, evidence-backed
local execution design is accepted.

## Consequences

- Desktop can package and supervise a real executable without Docker.
- Readiness is no longer dependent on a synthetic renderer fallback.
- The sidecar remains small enough for platform-specific packaging and smoke
  tests.
- Desktop users cannot yet execute Missions locally; that is an explicit product
  boundary, not a hidden fallback.

## Verification

- Sidecar unit tests validate the fixed loopback endpoint.
- Desktop native tests validate the same response contract and process
  supervision.
- Cargo and Tauri packaging checks remain required before bundle activation.
