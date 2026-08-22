# ADR-0086: Desktop Sidecar Readiness Health Contract

> Status: implemented  
> Owner: desktop maintainers  
> Date: 2026-08-22  
> Scope: `desktop/src-tauri/src/runtime.rs`, packaged Runtime sidecar

## Context

An alive child process does not prove that the local Runtime can accept work.
The desktop needs a bounded, local-only readiness signal before it enables the
Runtime as usable. The signal must not create a second Mission or WorkUnit
state model.

## Decision

The packaged sidecar listens only on `127.0.0.1:18097` and serves `GET /readyz`.
The response is HTTP 200 with a JSON body shaped as:

```json
{"protocolVersion":1,"status":"ready"}
```

`status` may be `starting` while dependencies are still initializing. The
desktop accepts `ready` only when `protocolVersion` equals the desktop runtime
protocol version. Connection refusal is `probing`; malformed responses,
non-200 responses, non-loopback endpoints, and protocol mismatches are
`unhealthy`. The request and response are bounded and have short timeouts.

The endpoint is fixed in the packaged launch specification and is never read
from renderer input or remote configuration. Mission Control and Runner
business state remain outside this protocol.

## Consequences

- Runtime liveness and readiness remain distinct in the UI and native contract.
- A compromised or misconfigured remote endpoint cannot be used as the local
  readiness signal.
- The sidecar packaging task must make the real Runtime serve this contract;
  the desktop will not substitute a mock response.

## Verification

- Native tests exercise HTTP response parsing, protocol-version rejection, and
  a real loopback TCP readiness exchange.
- Static checks must pass before packaging the sidecar binary.
