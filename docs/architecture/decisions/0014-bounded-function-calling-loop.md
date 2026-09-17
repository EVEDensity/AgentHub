# ADR-0014: Bounded Function-Calling Loop

> Status: accepted
> Owner: architecture maintainers
> Date: 2026-08-08
> Scope: Harness model and tool execution loop

## Context

ADR-0013 established a replaceable Harness boundary, but its initial
`SandboxHarness` could only perform one command. A useful agent execution path
needs normalized model responses, capability-scoped function calls, structured
tool feedback, and explicit limits against runaway loops.

The existing legacy AgentService and global tool registry contain transport and
session concerns that do not belong in the new Mission/WorkUnit execution path.

## Decision

Add a request-scoped `FunctionCallingHarness` with three replaceable boundaries:

- `ModelPort` returns normalized `ModelResponse` values;
- `FunctionTool` is an explicit per-run allowlist with argument validation and
  an async handler; and
- `FunctionResult` is fed back to the model without mutating Mission state.

The loop enforces a positive total timeout, maximum iterations, and maximum
function calls. Unknown tools, invalid arguments, handler failures, and
non-text handler results become unsuccessful structured feedback for the next
model turn. If the model produces final text, the Harness returns it as a
successful execution result. If a budget is exhausted first, it returns an
unsuccessful result and Runner records the WorkUnit failure.

This slice intentionally does not select a provider, persist checkpoints,
authorize tools from a database, or claim independent Evidence. Those concerns
remain future adapters and control-plane contracts.

## Consequences

The minimum Runner path can now execute a real provider-independent function
loop while preserving lease supervision and Artifact publication. Tool
capabilities are explicit at construction time, which makes the loop testable
and prevents accidental access to the legacy global registry. Model/provider
adapters can be added without changing Runner or Mission persistence.

The current function result channel is text-only and in-memory for one run.
Structured multimodal results, checkpoint replay, usage accounting, and
request-scoped policy evaluation require later versioned extensions.

## Alternatives considered

- Call the legacy AgentService tool loop from Runner: rejected because it owns
  session state and global tool discovery outside the new WorkUnit contract.
- Allow arbitrary tool names from model output: rejected because capability
  grants must be explicit and auditable.
- Retry indefinitely until the model returns text: rejected because a bounded
  execution must fail honestly when its loop budget is exhausted.

## Verification

- Harness tests cover final output, tool feedback, unknown-tool rejection,
  iteration limits, tool-call limits, argument validation, and total timeout.
- Runner tests verify function-harness output still follows the existing
  lease, Artifact registration, and completion path.

## Supersedes

None. This extends the Harness boundary introduced by ADR-0013.
