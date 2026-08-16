# ADR-0070: Contract-Owned Decision Governance SLA

> Status: accepted  
> Owner: Mission Control maintainers  
> Date: 2026-08-16  
> Scope: Mission Contract and Decision deadline policy

## Context

ADR-0063 made Decision expiry durable but sourced the initial deadline from an
injectable MissionService timeout. That process setting could differ between
replicas and was not part of the Mission's immutable delegation terms. Human
governance wait time is also distinct from execution time and retry budgets.

The application persistence path stores each v1 Contract as one JSON document
and exposes no update operation. Public v1 contracts allow additive optional
fields, so governance policy can be introduced without rewriting persisted rows
or breaking older clients.

## Decision

MissionContract gains an optional `governance` object containing
`decisionTimeoutSeconds`. The value is bounded from one second through one year.
An omitted v1 governance object resolves to the permanent v1 default of 86,400
seconds when the document is validated.

When Mission Control creates a verification Decision, it computes `expiresAt`
from the persisted Mission Contract and snapshots that timestamp on the
Decision. Process configuration no longer supplies or overrides this timeout.
Existing Decisions retain their stored deadline and are never recomputed.

This policy controls only human response time. Contract `expiresAt`, execution
budgets, Mission recovery, and revision of a running Mission remain separate
lifecycle concerns.

## Consequences

All replicas derive the same governance deadline from durable Mission truth.
Operators can select a response SLA when creating a new immutable Contract, and
legacy v1 documents retain the prior 24-hour behavior.

The default materializes during model serialization, so a newly persisted
Contract records the resolved policy. Historic JSON rows need no migration;
loading them produces the same fixed v1 default. Supporting multiple revisions
under one logical Contract identity still requires a separate persistence and
Mission-binding migration.

## Alternatives considered

- Keep a process setting: rejected because replica configuration is not Mission
  policy and can change after work starts.
- Reuse `budgets.timeSeconds`: rejected because execution and human governance
  consume different resources and have different failure semantics.
- Recompute existing Decision deadlines after a Contract change: rejected
  because it would mutate an active audit fact.
- Implement Contract revision and Mission rebinding in this slice: deferred
  because the current primary key and Mission reference do not identify an exact
  `(contractId, version)` pair.

## Verification

Domain and JSON Schema tests cover the stable default and bounds. Mission API
tests exercise a non-default Contract policy and prove that the durable Decision
deadline uses that exact value. The full Mission test suite protects existing
expiry, resolution, and event behavior.

## Supersedes

This ADR supersedes only ADR-0063's process-level source for newly created
Decision deadlines. ADR-0063's durable expiry semantics remain accepted.
