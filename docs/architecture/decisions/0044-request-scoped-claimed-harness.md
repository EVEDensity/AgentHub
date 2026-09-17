# ADR-0044: Request-Scoped Harness for Claimed Work

> Status: accepted  
> Owner: Runner and Harness maintainers  
> Date: 2026-08-15  
> Scope: claimed execution plans, capability binding, and model composition

## Context

ADR-0043 compiles a lease-fenced inbound context into bounded `text`, but
`WorkUnitRunner` still held one fixed Harness. A fixed function-calling Harness
cannot safely represent different WorkUnit capability scopes, attempt-correlated
MCP bindings, tool schemas, or Contract model-cost budgets. Falling back to the
default Sandbox Harness would also treat inbound text as an executable language.

The `a2a.receive` capability has a distinct role: it is an admission and Agent
selection marker. Exposing it as a model function or requiring a synthetic
no-op binding would confuse protocol eligibility with tool authority.

## Decision

A claimed resolver returns one `ClaimedWorkExecution` containing both the
bounded `RunnerExecutionInput` and the request-scoped `HarnessPort` authorized
to execute it. Runner validates the plan and uses its Harness for the claimed
attempt. Direct, explicitly supplied `run()` input continues to use Runner's
configured default Harness; claimed work never falls back to it.

The inbound composition root builds the Harness only after ADR-0043 identity
and lease-context validation. It parses the Contract and WorkUnit as domain
models, creates a `HarnessExecutionContext` from Mission, WorkUnit, and attempt,
and asks a binding factory for attempt-correlated capability bindings.
`a2a.receive` is removed from the callable tool requirements. Every remaining
required capability must be granted by the Contract and resolve to a concrete
binding. The exact resolved tools are passed to a model factory and to
`FunctionCallingHarness`; provider schemas and executable handlers therefore
derive from the same set.

Harness enforces the Contract model-cost budget and any lower local cap, along
with configured iteration, tool-call, token, and context limits. Invalid domain
context, missing bindings, malformed factories, and recursive
`a2a.outbound` adapter configuration fail closed. Factory failures are sanitized
before they can be recorded through Mission Control.

## Consequences

Capability scope and attempt identity now reach tools without entering the model
prompt, and each claim has an isolated Harness policy. The design can create
Stateless MCP bindings per attempt without retaining business sessions. It also
makes missing provider or tool configuration an honest execution failure.

This decision adds a composition root inside `app/`; it does not add a service
or claim an independently deployed Runner. A process entry point, credential
loading, polling/backoff, shutdown, and deployment health contract remain the
next operational slice and require explicit configuration rather than defaults.

## Alternatives considered

- Reusing one fixed Harness was rejected because tool scope and model schemas
  vary per WorkUnit and attempt.
- Putting capability scope in the prompt was rejected because prompt metadata
  is not authorization.
- Registering an `a2a.receive` no-op function was rejected because admission is
  not a callable tool.
- Fetching context a second time in a separate Harness resolver was rejected
  because it could compile input and authority from different snapshots.

## Verification

Runner tests prove claimed execution uses the plan Harness. Composition tests
cover per-attempt binding identity, Contract scope, exact model tool sets,
`a2a.receive` exclusion, missing-binding rejection, sanitized factory failure,
Contract cost enforcement, recursive-adapter rejection, and the complete claim,
model, Artifact registration, and VERIFYING transition path without Sandbox
fallback.
