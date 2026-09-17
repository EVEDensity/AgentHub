# ADR-0016: Contract-Scoped Function Tools

> Status: accepted
> Owner: architecture maintainers
> Date: 2026-08-09
> Scope: Mission Contract capabilities and Harness tool authorization

## Context

The bounded Harness accepts an explicit `FunctionTool` list, but the list was
previously assembled by callers without a direct link to a Mission Contract.
That leaves room for accidentally exposing tools which a WorkUnit does not
need, even when the Contract permits them for other WorkUnits.

## Decision

Add a fail-closed `CapabilityToolResolver`. Static `CapabilityToolBinding`
entries map one Contract capability to one or more function implementations.
For a run, the resolver exposes only capabilities listed in both the Contract's
`allowedCapabilities` and the WorkUnit's `requiredCapabilities`.

Every binding receives the immutable capability scope during argument
validation and execution. Missing grants, missing bindings, blank names, and
duplicate resolved function names reject the run before model execution. There
is no fallback to the legacy global tool registry. The resolved tools can be
rendered as OpenAI-compatible provider schemas without expanding their
authorization.

## Consequences

Tool access now follows least privilege at WorkUnit granularity and can enforce
repository paths, domains, or other Contract scope in one place. Registry
configuration errors fail explicitly instead of silently dropping protection.
Actual production bindings still need to be registered deliberately; this ADR
does not declare every legacy tool safe for Mission execution.

## Alternatives considered

- Expose every capability allowed by the Contract: rejected because a
  WorkUnit should receive only the subset it requires.
- Reuse the global ToolRegistry directly: rejected because registration is not
  equivalent to Contract authorization.
- Trust model-selected tool names: rejected because model output is untrusted
  execution input.

## Verification

- Resolver tests cover least-privilege selection, scope validation, provider
  schema generation, missing grants, missing bindings, and name collisions.
- Harness and Runner regressions continue to prove bounded execution and
  Mission Control ownership.

## Supersedes

None. This adds Contract authorization to ADR-0014 and ADR-0015.
