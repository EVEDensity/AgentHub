# Runner Component

> Status: implemented  
> Owner: execution maintainers  
> Last reviewed: 2026-08-15

## Responsibility

Runner owns one isolated execution attempt: lease-fenced start and heartbeat,
request-scoped Harness supervision, Artifact byte publication, metadata
registration, and completion to VERIFYING. It reports every durable change
through Mission Control and cannot create Evidence or declare success.

`RunnerWorker` is the process-local polling supervisor. The current minimum
worker polls one explicitly configured Mission ID through `claim_and_run`. This
is a deliberate vertical-slice limit, not a durable queue or fleet scheduler.
Global ready-work discovery remains a Mission Control responsibility.

## Inputs and outputs

- Input: a bound `WorkUnitRunner`, explicit Mission ID, lease duration, and
  bounded idle/error delay configuration.
- Output: lease-fenced Runner commands and a content-minimized in-process status
  snapshot for liveness/readiness adapters.
- Snapshot fields contain counters, timestamps, delay, and exception type only;
  they never contain objective, prompt, tool arguments, provider response, or
  credential content.

## Polling and shutdown

A successful empty claim marks the control path ready and exponentially backs
off to the configured maximum. A claimed WorkUnit resets delay to the minimum.
Any non-cancellation failure marks the worker unready, increments failure
counters, stores only the exception type, and continues with bounded backoff.

A requested stop prevents another poll but waits for an active claim to finish.
Task cancellation propagates into `WorkUnitRunner`; Runner's existing
cancellation supervision records the leased attempt as failed before re-raising
when Mission Control is reachable. The worker never swallows cancellation.

## Process boundary

`services/python/runner_service/` is the strict minimum process adapter. It
requires explicit identity and URL configuration, loads separate Mission
Control, AI Gateway, and MCP credentials from mounted files, rejects mock model
routing, forwards the exact resolved tool schemas, builds Stateless MCP
bindings per attempt, and exposes sanitized `/healthz` and `/readyz` probes.
Shutdown drains the active claim until a bounded deadline and then cancels it
through the existing Runner supervision path.

This is a single-Mission deployment candidate, not a production fleet
scheduler. Its readiness proves a successful Mission Control claim request; it
does not execute model or tool probes outside a WorkUnit. Mission Control owns
the workspace ready-work contract described below; the next scale gate is for
Runner to consume it instead of adding a local queue.

## Ready-work discovery

Mission Control now exposes a workspace-scoped atomic discovery contract. It
filters by immutable Agent/adapter binding, allows only delegated or eligible
inbound-root work, checks dependency readiness, orders by least in-flight
Mission load, and locks the owning Mission plus candidate WorkUnit with
`SKIP LOCKED`. Authentication supplies the lease owner; callers cannot provide
one in the request.

The process worker has not yet switched to this endpoint and still requires an
explicit Mission ID. The next slice is a consumer-only migration: replace the
worker's fixed-Mission poll input with explicit workspace scope while preserving
the same backoff, readiness, shutdown, claim validation, and execution path.
