# ADR-0069: Decision Expiry Operational Metrics

> Status: accepted  
> Owner: Mission Control and operations maintainers  
> Date: 2026-08-16  
> Scope: Decision expiry process observability and alerting

## Context

The Decision expiry supervisor has a real transactional PostgreSQL path and a
passing isolated smoke gate, but health and readiness JSON require direct probe
inspection. Operators need time-series signals when database polls repeatedly
fail or a live worker stops completing them.

Decision, WorkUnit, Mission, workspace, tenant, and exception values would make
unsafe or unbounded metric labels. Process counters also reset on restart and
cannot represent durable lifecycle totals or backlog truth.

## Decision

The service exposes a Prometheus text endpoint at `/metrics`. It projects only
fixed-name process health, readiness, poll, expiry, idle, failure, backoff, and
last-success values from the existing supervisor snapshot. It exports no
dynamic labels and performs no database query while being scraped.

The supervisor records the latest successful database poll separately from the
latest attempted poll. Failed polls do not refresh that timestamp. Prometheus
uses a dedicated `mission-supervision` scrape job. Warning alerts detect three
or more sustained consecutive failures and an alive worker with no successful
poll for more than one minute. Each deployment must add the service to its own
Prometheus discovery under a dedicated `mission-supervision` job because the
repository's concrete Prometheus topology is intentionally private.

These alerts indicate supervisor operation only. They do not claim that every
expired Decision met its SLA, that no backlog exists, or that Mission state is
correct. Durable state and Mission events remain the audit source of truth.

## Consequences

Operators can alert on a stalled or failing expiry loop without exposing
business identifiers or adding a queue, cursor, metrics table, or database read
to the scrape path. Counters reset when a process restarts, and target-down
alerting remains a deployment-level responsibility because the local service
profile is intentionally optional.

## Alternatives considered

- Put Mission or Decision IDs in labels: rejected for cardinality and data
  exposure.
- Query pending Decision counts on every scrape: rejected because metrics must
  not create a second polling path or add load to the lifecycle database.
- Persist counters: rejected because Mission events already own durable audit
  truth and operational counters do not justify another write model.
- Reuse readiness as the only alert: rejected because it cannot distinguish a
  dead task from repeated database failures or quantify recovery.

## Verification

Service tests validate Prometheus content type, metric values, and content
sanitization. Deployment contract tests validate alert names, expressions, and
absence of business-identifier labels; the deployment README defines the
required scrape-discovery shape.

## Extends

This decision operationalizes the deployment candidate and smoke gate from
[ADR-0067](0067-isolated-decision-expiry-smoke-gate.md).
