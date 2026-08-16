# ADR-0067: Isolated Decision Expiry Smoke Gate

> Status: accepted  
> Owner: Mission Control, operations, and test maintainers  
> Date: 2026-08-16  
> Scope: real PostgreSQL and container verification for expiry supervision

## Context

ADR-0066 requires a direct PostgreSQL transaction and file-mounted DSN, but unit
and static Compose tests cannot prove that the image starts read-only, Alembic
head accepts the schema, PostgreSQL locking works through the service pool, or
the independently running supervisor produces the complete durable closure.

Using the developer's `.env` database for a smoke test would risk expiring real
Decisions. Reusing the full platform Compose would also mix unrelated services,
fixed ports, credentials, and persistent data into a narrow lifecycle gate.

## Decision

AgentHub provides a dedicated Decision expiry smoke Compose topology containing
only PostgreSQL and the supervisor. Both published ports bind to loopback with
Docker-assigned random host ports. PostgreSQL uses a generated per-run password,
and the service DSN is a generated file in an operating-system temporary
directory mounted through Compose secrets.

A Python orchestrator assigns a process-unique Compose project, supplies an
explicit temporary `--env-file`, and removes ambient `DATABASE_URL`. It starts
PostgreSQL, resolves the random port, runs Alembic to head against that database,
and inserts one internally consistent PENDING Decision whose persisted expiry is
in the past. It then builds and starts the real supervisor image.

The gate verifies EXPIRED/FAILED/FAILED aggregate state, service-owned Decision
closure metadata, absence of Evidence, exactly three aggregate events, and the
Decision-to-WorkUnit-to-Mission causation chain. It also verifies sanitized
readiness counters and checks that later idle polls do not duplicate events.

Cleanup runs in `finally` with the exact project name and `down --volumes
--remove-orphans`. Cleanup failure fails a successful smoke run. When both the
test and cleanup fail, the original test error remains primary and only the
cleanup error type is printed, avoiding credential or DSN disclosure.

## Consequences

The expiry deployment candidate gains a reproducible end-to-end gate without
access to existing Mission data. The gate exercises a real image, migration
chain, direct transaction, health probe, and shutdown cleanup rather than
manufacturing success through mocks.

Docker availability and image/package downloads are explicit prerequisites, so
the gate remains opt-in rather than part of every unit-test invocation. CI may
promote it to a required infrastructure job once runner capacity and image cache
policy are defined.

Passing this gate proves the isolated candidate topology, not production secret
manager integration, network policy, alerting, backup, or capacity behavior.

## Alternatives considered

- Point the supervisor at `.env`: rejected because its ownership and data are
  unknown and the command performs real terminal transitions.
- Mock PostgreSQL or call `expire_next_decision()` in process: rejected because
  those paths are already covered and do not verify container composition.
- Reuse the full platform Compose: rejected because unrelated dependencies and
  fixed ports make the gate slower and less isolated.
- Keep smoke data after success: rejected because the database is disposable
  evidence, not a new durable test environment.

## Verification

Static tests validate the two-service topology, lack of persistent volumes,
random loopback ports, secret mount, readiness dependency, port parsing, and
sanitized counter assertions. The opt-in smoke command validates migration,
container startup, durable state, event causation, idempotent idle polling, and
cleanup against real PostgreSQL.

## Extends

This decision implements the direct-PostgreSQL smoke requirement from
[ADR-0066](0066-file-backed-transactional-supervisor-database.md).
