# ADR-0095: Runner Runtime Resource Lifecycle

> Status: implemented  
> Owner: execution maintainers  
> Date: 2026-08-22  
> Scope: `services/python/runner_service/runtime.py`

## Context

The Runner service owns one worker task and the HTTP clients used by Mission
Control, the model gateway, and Stateless MCP. Shutdown can be initiated by a
normal application lifespan or by startup failure. Repeating shutdown must not
close those clients twice, and a stopped runtime must not be accidentally
restarted with a worker whose cancellation state is already terminal.

## Decision

`RunnerServiceRuntime.stop()` is idempotent. The first stop drains or cancels
the worker, clears the task handle, closes owned resources once, and marks the
runtime stopped. Subsequent stops return without touching resources. `start()`
rejects a runtime after resources have been closed and continues to reject a
second start while active.

## Consequences

- FastAPI lifespan cleanup and startup-failure cleanup can share the same stop
  path safely.
- Resource ownership remains explicit and reverse-order close is preserved.
- Restart requires constructing a fresh runtime with fresh HTTP clients and
  worker state, which avoids stale leases and cancellation events.

## Verification

- Runner service entrypoint tests cover graceful stop, deadline cancellation,
  idempotent stop, and restart rejection.
