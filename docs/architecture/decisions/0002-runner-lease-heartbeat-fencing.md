# ADR-0002: Runner Lease Heartbeat Fencing

> Status: accepted  
> Owner: architecture maintainers  
> Date: 2026-08-08  
> Scope: Mission Control WorkUnit execution lifecycle and Runner control API

## Context

Lease acquisition and controlled `LEASED -> RUNNING` startup already fence a
WorkUnit by lease ID, runner ID, and expiry. Without renewal, a valid long-
running attempt can expire while it is still executing; blindly accepting late
updates would let a stale runner continue mutating durable state.

## Decision

Mission Control exposes a runner-scoped heartbeat operation for a WorkUnit.

- A heartbeat requires the current `leaseId` and authenticated runner identity.
- Only `LEASED` and `RUNNING` WorkUnits with an unexpired matching lease may be
  renewed.
- Renewal keeps the lease ID and runner ID, and sets a bounded new expiry from
  the server's UTC clock.
- Renewal is transactional with the WorkUnit snapshot update and appends a
  `work_unit.lifecycle.heartbeat` event containing the old and new expiry.
- An expired, mismatched, or terminal lease is rejected with a conflict; it
  cannot be revived by heartbeat. Expired work must use the recovery path.
- Recovery returns a WorkUnit to `RETRYING` only while its Contract retry
  budget permits another attempt. Once exhausted, recovery transitions the
  WorkUnit and its Mission to `FAILED` and records the budget exhaustion in
  the lease-expired event.
- Heartbeat does not imply completion, verification, artifact creation, or a
  Mission terminal transition.

## Consequences

Runners can safely maintain ownership during execution, and stale runners are
fenced by the same durable lease identity used for startup and terminal
commands. The system records heartbeat history, adding event volume proportional
to runner activity. Clients must renew before expiry and use recovery when a
lease has already expired. Recovery cannot create attempts beyond the immutable
Contract retry budget.

## Alternatives considered

- Extend a lease without checking runner identity: rejected because stale or
  compromised runners could take over active work.
- Allow renewal after expiry: rejected because it revives work after fencing
  and makes recovery nondeterministic.
- Keep heartbeat only in process memory: rejected because restart would lose
  the ownership signal and observability history.

## Verification

- Mission API tests cover active renewal, expiry extension, mismatched lease
  rejection, expired lease rejection, recovery, retry-budget exhaustion, and
  event persistence.
- The service validates the lease duration to the same one-hour bound as the
  HTTP schema.
- Existing lease/start/recovery tests continue to pass, preserving the
  `LEASED -> RUNNING` and expired-lease recovery contracts.

## Supersedes

None.
