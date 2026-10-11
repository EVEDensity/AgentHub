# ADR-0116: Runner contact and honest chat execution status

> Status: accepted
> Owner: Mission Control and frontend maintainers
> Date: 2026-10-09
> Scope: workspace claims, operational presence, execution projection, chat UI

## Context

An enabled catalog Agent describes an execution binding. It does not prove a
matching Runner is connected or that its model is ready. Mission admission can
produce a RUNNING Mission containing a PENDING WorkUnit while no Runner exists.
The chat UI previously displayed an optimistic thinking message and waited for
SSE completion before acknowledging admission. A connected event stream also
does not establish execution progress.

## Decision

Mission Control retains all Mission, WorkUnit, and lease authority. A new
`runner_presence` table stores bounded operational contact observations from
successful, authenticated, workspace-authorized claim requests. The server
records its clock and a fixed 30-second expiry; clients cannot supply a TTL or
heartbeat timestamp. A record identifies workspace, Runner principal, Agent,
adapter, WorkUnit kind, and explicitly declared supported capabilities.

Workspace claim requests gain optional `supportedCapabilities`; old consumers
default to an empty set. A presence match requires the exact workspace, Agent,
adapter and kind, plus every WorkUnit required capability. Catalog tags cannot
establish tool support. Expired, malformed, future-dated, and overlong observation
windows cannot report availability. A matching live execution lease also proves
contact for its binding/kind and exact required-capability snapshot. Neither
source guarantees provider readiness or permits tool use beyond the Contract.
Runner producers accept an explicitly configured, bounded and unique capability
tuple, default empty; the workspace composition forwards only declarations that
were actually configured. It does not infer capabilities from tool names or
catalog metadata, and does not alter business claim selection.

Presence writes follow successful claim admission. An observation write failure
must preserve the committed claim response so the Runner receives its lease;
the server logs only the error type and creates no substitute observation.
Execution-status reads fail with 503 on database errors, rather than representing
an outage as an offline Runner.

`GET /api/v1/missions/{missionId}/execution-status` authorizes the Mission's
persisted workspace and projects durable WorkUnit states alongside matching
Runner contact. A PENDING unit remains waiting even when a Runner is available.
Execution requires a RUNNING unit and an unexpired lease. Expired RUNNING leases
are displayed as requiring recovery, without rewriting durable state.

The frontend returns from POST admission immediately, polls the read projection,
and treats SSE only as event transport. Status retries and stream reconnects do
not create another Mission. Cancellation calls the authorized server command
and reports success only after its response. Confirmation-required admission
renders explicit confirm/cancel controls. Catalog entries are registered bindings,
and unavailable catalog reads do not insert production Agent fixtures.
Cancellation responses are applied only while both conversation identity and
the target Mission or confirmation still match the initiating command; a delayed
response cannot stop a newly admitted Mission's event stream.

Legacy conversation IDs are never used to assign v1 workspace scope. The UI
links its conversation view to a newly admitted v1 session and caches only the
session ID returned by the server. Every subsequent request still passes server
ownership authorization. This cache does not migrate or authorize legacy rows.

## Compatibility and rollback

PostgreSQL revision `d9a3b425e6f7` follows `c8f2a314d5e6`; SQLite schema version 5
installs observation DDL and its marker transactionally. Upgrades preserve all
Mission and conversation data. PostgreSQL downgrade drops only the ephemeral
observation table. Reupgrade starts offline until an authorized Runner polls
again; no presence or successful execution is reconstructed from PENDING state.

## Verification

Real SQLite boot/API tests cover ordinary workspace users, successful and denied
polls, exact matching, expiry/corruption, live and expired leases, and database
outages. Migration tests verify version-4 upgrades, rollback on DDL failure,
PostgreSQL storage, capability matching, and safe downgrade/reupgrade. Frontend
tests cover immediate acknowledgement, confirmation, cancellation, scoped native
session IDs, reconnect deduplication, and read retries. Browser contract tests
verify an explicitly selected Agent without a Runner remains visibly PENDING.
