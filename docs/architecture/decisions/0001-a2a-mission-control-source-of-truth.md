# ADR-0001: A2A Mission Control Source Of Truth

> Status: accepted  
> Owner: architecture maintainers  
> Date: 2026-08-08  
> Scope: Python Mission Control, Go Gateway A2A adapter, frontend A2A command surface, and local deployment

## Context

The A2A Gateway previously kept task lifecycle state in a process-local map.
That made task lookup dependent on process memory, allowed fabricated status
updates, and made cancellation and dispatch failures impossible to reconcile
with the durable execution model. The repository architecture already defines
Mission and WorkUnit as the business truth, while A2A is only a protocol
adapter.

## Decision

The Python Mission Control A2A adapter is the only durable source of truth for
inbound A2A task lifecycle operations.

- `tasks/send` creates or idempotently retrieves a Mission and mapped WorkUnit
  before forwarding to the selected external Agent.
- `tasks/get` and `tasks/cancel` read and mutate that control-plane state using
  an explicit `workspaceId` and the caller's Bearer token.
- Gateway dispatch failures are written back as `FAILED`; remote protocol
  responses are acknowledgements and cannot by themselves prove completion.
- The Go Gateway has no in-memory A2A task fallback. Its optional registry
  remains limited to Agent Card discovery and routing metadata.
- `MISSION_CONTROL_PLANE_URL` configures the Gateway endpoint; local Compose
  resolves the host Python API through `host.docker.internal`.

Runner, Harness, artifact storage, and independent evidence remain separate
follow-up boundaries. They are responsible for turning an accepted WorkUnit
into a verified terminal outcome and are not inferred by this adapter.

## Consequences

Task state survives Gateway restarts and is consistent with Mission/WorkUnit
transitions. Requests now require workspace identity and control-plane
availability, so deployments must configure the Python API and valid auth
tokens. A remote Agent can be unavailable without creating a false success;
the failure is visible in the durable Mission history. The adapter adds one
control-plane round trip before dispatch and another only when failure must be
recorded.

## Alternatives considered

- Keep a Gateway-local cache as the fallback: rejected because it creates a
  second business truth and loses state on restart.
- Add a dedicated A2A task table: rejected because it duplicates Mission and
  WorkUnit lifecycle semantics.
- Mark a task completed from the remote A2A response: rejected because the
  adapter cannot verify artifacts or independent evidence.

## Verification

- Python A2A API and domain transition tests cover idempotent mapping,
  cancellation, failure write-back, and `PENDING -> FAILED`.
- Go Gateway tests cover control-plane contract calls, auth forwarding,
  durable submit/get/cancel behavior, and remote dispatch failure recording.
- Frontend store tests assert explicit workspace propagation and honest
  unbound-user behavior.
- Compose YAML validation asserts the control-plane URL and host mapping.

## Supersedes

None.
