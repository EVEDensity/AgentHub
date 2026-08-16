# Harness Component

> Status: implemented
> Owner: execution maintainers
> Last reviewed: 2026-08-15

## Responsibility

Harness owns one bounded WorkUnit model/tool execution loop. It normalizes
provider responses, validates and invokes the capability-scoped tool set,
enforces iteration, tool-call, timeout, token, and model-cost limits, and
produces a `HarnessResult` for Runner.

Harness may emit request-scoped execution events and checkpoints through
`HarnessCheckpointPort`. The default production boundary does not require a
checkpoint implementation. The supplied in-memory port is intended for one
execution attempt, local supervision, and tests; it is not a durable event
ledger or a restart recovery mechanism.

Mission Control separately owns content-minimized durable
`ExecutionCheckpoint` records. Its lease-fenced command persists identity,
phase, counters, usage, terminal state, and a server-generated digest, but never
persists Harness tool results or model content. The production adapter is not
wired yet; durable checkpoints are ancestry anchors and do not claim restart-
safe model-loop resumption.

## Inputs

- `HarnessRequest`: code, language, timeout, working directory, and optional
  `HarnessExecutionContext` containing Mission, WorkUnit, and attempt IDs.
- Inbound A2A input is a deterministic `text` JSON document compiled from a
  lease-fenced Mission/Contract/WorkUnit projection. The peer objective is
  explicitly untrusted data; capability metadata in the document never grants
  a tool.
- `ModelPort`: provider-independent model completion responses.
- `FunctionTool`: explicit capability-granted handlers and argument validators.
- Claimed inbound work supplies a request-scoped Harness as part of one
  execution plan. Its model factory receives the exact resolved `FunctionTool`
  set, and its binding factory receives Mission, WorkUnit, and attempt identity.
- Optional `HarnessCheckpointPort`: atomic checkpoint/event recording.

## Outputs

- `HarnessResult`: sandbox-compatible final output or an honest failure, loop
  counters, and cumulative `ModelUsage`.
- `HarnessEvent`: content-minimized lifecycle metadata for iteration, model,
  tool, budget, and terminal execution events.
- `HarnessCheckpoint`: the request-scoped loop state associated with each event.

## Failure behavior

Unknown tools, invalid arguments, and handler failures become structured model
feedback. Loop, timeout, token, and cost limits return unsuccessful results;
budget events are emitted before the terminal failure event and no later tool
call is executed. Provider failures return an unsuccessful result with a
sanitized exception type. A checkpoint adapter failure raises `HarnessError`
before the next model or tool side effect. Runner converts execution failures
into the existing lease-fenced WorkUnit failure path and never publishes an
Artifact.

## Dependencies and boundaries

Harness depends on replaceable model, tool, sandbox, and checkpoint ports. It
does not import Mission repositories, write WorkUnit state, persist prompts or
tool arguments, or claim independent Evidence. Mission Control remains the
durable source of lifecycle truth; future observability or event-ledger
adapters consume this boundary without changing Harness policy.

`SandboxHarness` does not execute inbound `text` as a shell or program. A
deployment that accepts inbound A2A execution must explicitly configure a
model-capable Harness such as `FunctionCallingHarness`, with tools resolved
from the Contract and WorkUnit through the separate capability resolver.
Claimed execution cannot fall back to Runner's fixed Harness: resolution must
return both bounded input and a valid request-scoped Harness. The protocol-only
`a2a.receive` admission capability is never exposed as a model function; every
other required capability must have a concrete binding. The model factory must
receive those resolved tools so provider tool schemas and executable handlers
cannot drift.
