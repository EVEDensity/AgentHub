# ADR-0017: Harness Usage and Cost Budgets

> Status: accepted
> Owner: architecture maintainers
> Date: 2026-08-09
> Scope: Harness model usage accounting and bounded execution

## Context

The Harness already bounds wall-clock time, iterations, and function calls,
but model providers can also impose token and spend limits. Without usage
accounting, a tool loop can remain within its iteration budget while exceeding
an operational or commercial limit. Provider adapters also expose usage in
different shapes, so budget policy must not be implemented in Runner or in a
provider-specific loop.

## Decision

Add request-scoped `ModelUsage` to the Harness `ModelResponse` and
`HarnessResult` contracts. Usage contains provider-reported prompt tokens,
completion tokens, and cost, validates non-negative values, and accumulates
across model turns. `FunctionCallingHarness` checks cumulative total tokens
and model cost immediately after every model response and terminates before
executing any returned tools when a configured budget is exceeded.

`ModelAdapterPort` reads the adapter's per-call `last_usage` mapping and
calculates cost from configured prompt and completion token prices. Missing or
malformed provider usage is treated as zero; the adapter does not invent a
usage estimate. Pricing configuration is validated as non-negative. Usage is
returned as execution metadata and is not persisted as Mission business
state by Runner.

## Consequences

Budget enforcement remains inside Harness and applies consistently to every
Runner execution that uses the port. Runner receives a failed Harness result,
so it can record the WorkUnit failure without publishing an Artifact. Cost
rates remain deployment/provider configuration and can be changed without
altering Mission schemas. Providers that do not report usage remain usable,
but their calls cannot consume a non-zero configured budget until they expose
the usage mapping.

## Alternatives considered

- Enforce token limits in Runner: rejected because it would duplicate the
  model loop policy outside Harness.
- Estimate tokens from character length: rejected because estimates are not
  auditable provider usage and can silently mischarge a user.
- Persist usage as WorkUnit state: rejected because usage is execution
  telemetry, while Mission Control owns durable lifecycle truth.

## Verification

- Harness tests cover cumulative usage, total-token termination before tools,
  cost termination, and non-negative validation.
- Model adapter tests cover pricing, missing/invalid usage, and negative price
  rejection.
- Runner, Artifact, Mission API, persistence, migration, and A2A regressions
  remain green.

## Supersedes

None. This extends ADR-0014 and ADR-0015 with bounded usage accounting.
