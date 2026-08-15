# ADR-0048: Workspace-Scoped Runner Consumer

> Status: accepted  
> Owner: Runner and Mission Control maintainers  
> Date: 2026-08-15  
> Scope: Runner polling, process configuration, and deployment

The shared-identity authorization limitation described below is replaced by
[ADR-0049](0049-runner-workspace-grants.md). Tenant concurrency admission is
added by [ADR-0050](0050-tenant-runner-concurrency-admission.md).

## Context

ADR-0047 added atomic workspace ready-work discovery to Mission Control. The
strict Runner process still polls one configured Mission, which requires one
process per Mission and leaves the new discovery contract unused. Runner must
not close this deployment gap by listing Missions, retaining candidates, or
creating a second scheduling cursor.

## Decision

Add `WorkUnitRunner.claim_ready_and_run(workspace_id)` and make
`RunnerWorker` use it. The worker holds only the workspace, immutable
Agent/adapter binding, lease duration, backoff state, and at most one active
attempt. `RunnerServiceSettings.workspace_id` replaces `mission_id`, exposed as
`AGENTHUB_RUNNER_WORKSPACE_ID`.

The workspace claim response must contain a non-empty WorkUnit `missionId`.
Runner validates that Mission identity together with `LEASED` state, exact
Agent/adapter binding, lease owner, lease ID, and attempt before resolving
trusted context or starting execution. After validation, workspace and
Mission-scoped claims share the same resolver, start, heartbeat, cancellation,
Artifact publication, registration, and completion path.

The existing Mission-scoped `claim_and_run` method and API remain available for
compatibility. They are not used by the strict process worker.

## Consequences

One process can execute sequential eligible WorkUnits across Missions in an
authorized workspace. Multiple replicas may safely share the same workspace
and binding because discovery, fairness, dependency checks, and row locking
remain transactional in Mission Control. Runner gains no durable queue,
scheduling cursor, Mission scan, priority policy, or authority to declare
success.

The minimum direct authorization path uses one workspace-scoped service
identity shared by replicas, with attempts separated by lease ID. It must not
use administrator credentials. Distinct service-principal identities require a
separate workspace-grant contract before they can replace the shared identity.

This is still not capacity-aware fleet scheduling. Priority, quotas, model or
hardware affinity, and per-tenant concurrency must be added to Mission Control
policy with explicit contracts and tests.

## Verification

Runner tests cover workspace polling, empty/error backoff, shutdown and
cancellation, extraction of the claimed Mission identity, rejection of a
missing Mission ID before execution, and continued Mission-scoped compatibility.
ASGI integration tests drive concurrent control clients through claim, start,
Artifact registration, and completion without duplicate execution. A dedicated
CI PostgreSQL service forces concurrent claims to hold separate row locks and
proves `SKIP LOCKED` selects distinct WorkUnits. Service configuration and
composition tests retain file-backed credentials, strict adapters, and
sanitized readiness behavior.
