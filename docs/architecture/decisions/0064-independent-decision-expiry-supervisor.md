# ADR-0064: Independent Decision Expiry Supervisor

> Status: accepted  
> Owner: Mission Control and operations maintainers  
> Date: 2026-08-16  
> Scope: Decision expiry process topology and operational lifecycle

## Context

ADR-0063 added the transactional command that closes one expired Decision and
its blocked WorkUnit and Mission, but intentionally did not poll inside the Web
process. Automatic expiry needs an independently runnable lifecycle, bounded
polling, startup validation, probes, and graceful shutdown. It must not create a
second scheduler or move lifecycle ownership away from Mission Control.

## Decision

AgentHub provides a standalone Python Decision expiry service. The process
composes the existing `MissionService` over the Mission Control repository and
uses the same required PostgreSQL `DATABASE_URL`. Database connectivity is
checked before the worker task starts. The process is a Mission Control
maintenance role, not a general Agent, Runner, Verifier, or protocol adapter.

The supervisor invokes `expire_next_decision()` for one item at a time. A
successful expiry immediately invokes the command again to drain available
work. Idle results and transient exceptions use bounded exponential backoff.
Cancellation propagates through an active command; graceful shutdown first
requests stop and waits for the configured deadline before cancelling and
closing the process-owned database pool.

The supervisor persists no queue, cursor, claimed row, retry record, or copy of
domain data. It does not accept a workspace or Decision TTL. The persisted
`expiresAt` remains authoritative, and the command's database locks make
multiple service replicas safe to run concurrently.

The service exposes only `/healthz` and `/readyz`; API documentation is
disabled. Health means the worker task is alive. Readiness requires at least one
successful real command invocation, including an idle result, and becomes false
after a failed poll. Probe data is restricted to process state, bounded delay,
low-cardinality counters, timestamps, status enum, and exception class name.

The initial stage ships a runnable module and non-root container but does not
enable a deployment replica. Deployment wiring follows only after a real
PostgreSQL smoke test and operational probe validation.

## Consequences

Decision expiry no longer depends on Web request traffic or a Web-process
background task. Operators can scale or stop supervision independently without
creating another source of lifecycle truth. A database outage keeps the process
alive after startup but not ready, and polling recovers with bounded load.

The process has Mission Control database authority and must therefore use the
same secret-management and network controls as Mission Control. It is not an
untrusted edge service. Stopping all replicas pauses automatic expiry without
changing persisted deadlines or manufacturing success.

This stage does not add metrics export, alert rules, immutable Contract-based
governance SLA, or recovery of an EXPIRED Mission. Those remain separate slices.

## Alternatives considered

- Poll in FastAPI lifespan: rejected because Web replica count and restarts
  should not control lifecycle supervision.
- Add a durable queue or NATS subject: rejected because PostgreSQL already owns
  eligibility and locking, and another durable cursor would duplicate truth.
- Recompute expiry from process configuration: rejected because it would change
  existing Decision semantics after creation.
- Expose expiry as a public HTTP command: rejected because this maintenance
  slice needs no new externally authorized mutation surface.
- Wait after every expired item: rejected because a backlog should drain without
  injecting idle latency between transactions.

## Verification

Worker tests cover multi-item drain, bounded idle/error backoff, wait
interruption, graceful stop, task cancellation, invalid outcomes, concurrent
run rejection, and sanitized snapshots. Runtime tests cover database startup,
resource closure, shutdown cancellation, startup failure cleanup, strict
composition, and redacted health/readiness responses.

## Extends

This decision implements the separately deployable supervisor follow-up from
[ADR-0063](0063-fail-closed-decision-expiry.md).
