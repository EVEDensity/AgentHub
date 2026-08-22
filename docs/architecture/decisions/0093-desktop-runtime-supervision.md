# ADR-0093: Desktop Runtime Supervision Boundaries

> Status: accepted  
> Owner: desktop maintainers  
> Date: 2026-08-22  
> Scope: `desktop/src-tauri/src/runtime.rs`

## Context

The desktop launcher starts a local Runtime sidecar, but process liveness alone
does not prove readiness. A crashed or stale child must not remain after the
launcher exits, and a port conflict must not cause the launcher to claim or
terminate a process it did not start.

## Decision

`LocalRuntime` owns only children spawned by its own `start` operation. It
reaps that child on explicit stop and in its destructor. Before spawning, it
checks whether the configured loopback health port is available and returns a
readable failure when another process owns it. After spawning, readiness is
polled through the versioned `/readyz` contract; a child that remains
unready for 15 seconds is terminated and reported as failed. Repeated start
requests while the child is starting or running remain idempotent.

Diagnostics expose lifecycle state and fixed, non-sensitive explanations. The
desktop does not capture or return sidecar stdout, stderr, credentials, or
request payloads.

## Consequences

- Normal application exit does not leave a desktop-owned sidecar behind.
- Port conflicts and startup hangs are actionable without leaking secrets.
- External processes are never killed by stale-PID heuristics.
- A slow but valid Runtime must become ready within the bounded startup window;
  longer initialization requires a protocol or product decision.

## Verification

- Native tests cover child reaping, port occupancy, startup state, crash state,
  and versioned readiness.
- `cargo fmt --check`, `cargo clippy -- -D warnings`, and the Tauri test suite
  pass on the pinned Windows toolchain.
