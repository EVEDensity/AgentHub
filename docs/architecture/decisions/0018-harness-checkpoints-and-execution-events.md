# ADR-0018: Harness Checkpoints and Execution Events

> Status: accepted
> Owner: architecture maintainers
> Date: 2026-08-09
> Scope: Harness execution observability and request-scoped checkpoints

## Context

The Harness loop now enforces tools and budgets, but a failed or long-running
attempt has no structured execution trace and no inspectable intermediate
state. Adding persistence directly to Harness would create a second source of
Mission truth and would couple model execution to one event infrastructure.

## Decision

Add a `HarnessExecutionContext` containing the Mission ID, WorkUnit ID, and
lease attempt. Runner passes this context into `HarnessRequest` after the
controlled WorkUnit start.

Add a request-scoped `HarnessCheckpointPort` whose atomic `record` operation
receives a content-minimized `HarnessEvent` and its corresponding
`HarnessCheckpoint`. The in-memory implementation is single-execution and
validates contiguous sequence numbers. Events cover execution start and
terminal state, iteration start, model start/completion, tool start/completion,
and budget exhaustion. Events carry correlation, counters, cumulative usage,
and safe status metadata; prompts, model text, tool arguments, and tool result
content are excluded from events. Checkpoints retain the bounded tool-result
feedback needed to inspect the current request-scoped loop.

Recording occurs before the next model or tool side effect. A checkpoint port
failure raises `HarnessError`; Runner therefore records a WorkUnit failure and
does not publish an Artifact. The port is an adapter boundary: it does not
write Mission repositories or the durable event ledger.

## Consequences

Local tests and supervisors can inspect ordered execution progress without
changing the model loop or Mission schemas. A future event-ledger,
OpenTelemetry, or durable checkpoint adapter can implement the same port after
retention, ACL, redaction, and replay semantics are specified. The current
in-memory implementation intentionally does not survive process restart and
does not resume an interrupted Harness attempt.

## Alternatives considered

- Append Harness events directly to Mission Control: rejected because
  execution telemetry is not WorkUnit lifecycle truth and would create a
  cross-process write dependency.
- Persist every prompt, tool argument, and model response: rejected for this
  slice because it expands sensitive-data retention before redaction and ACL
  policy exists.
- Add a second Runner execution state machine for replay: rejected because
  Harness remains the owner of loop state and Runner remains a supervisor.

## Verification

- Harness tests verify correlated event ordering, contiguous sequences,
  cumulative usage, tool-result checkpoints, budget termination events,
  request-scoped reuse rejection, and fail-closed checkpoint errors.
- Runner tests verify Mission/WorkUnit/attempt context propagation.
- Existing Harness, Runner, Artifact, Mission API, persistence, migration, and
  A2A regression suites remain required before commit.

## Supersedes

None. This extends ADR-0014, ADR-0015, and ADR-0017.
