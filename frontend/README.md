# Frontend

The Next.js frontend is a projection and command surface for the control plane.
It must not manufacture domain state when a backend is unavailable.

## Areas

- `app/`: admin and application routes.
- `components/`: reusable UI and workflow views.
- `stores/`: transport state and cache; no durable business truth.
- `types/`: API-facing types; prefer generated or contract-backed types for new
  Mission and WorkUnit endpoints.
- `e2e/`, `__tests__/`: browser and component verification.

Demo fixtures are permitted only in explicitly named development stories or
tests. Production failure must render an honest unavailable/error state.

## Mission chat execution

Chat admission returns after `POST /api/v1/chat/mission` persists the Mission
and its PENDING WorkUnit. The execution banner reads the workspace-authorized
`GET /api/v1/missions/{missionId}/execution-status` projection. It distinguishes
waiting for a matching Runner, waiting for claim/start, running behind a live
lease, independent verification, governance, and terminal outcomes. An enabled
catalog binding or connected SSE stream never becomes an execution signal.

Submission and read errors remain visible. Status retries reread the same
Mission; stream reconnects deduplicate durable event IDs. Cancellation issues
the server command and shows success only after acknowledgement. Pending rule
confirmations expose explicit confirmation and cancellation buttons. Expired
leases cannot display active execution.

The conversation view caches IDs of native v1 sessions returned by admission;
legacy session IDs do not acquire inferred workspace ownership. Workspace
selection uses the authenticated user's scope, and cache entries never replace
API authorization. Session changes clear transport events and cursors. Catalog
reads render registered Agents or a visible unavailable state without fixtures.

See [ADR-0116](../docs/architecture/decisions/0116-runner-contact-and-chat-execution-status.md).

When adding a user workflow, document its command, loading, retry, cancellation,
and permission states and cover the primary path with an end-to-end test.

## Decision inbox

The admin Decision inbox is the human command surface for pending Mission
Control Decisions in the selected workspace. It reads
`GET /api/v1/missions/decisions` and resolves an item through the versioned
`POST /api/v1/missions/{missionId}/decisions/{decisionId}/resolve` command.

- Loading and workspace changes fetch server state and cancel stale reads.
- Read or command failures remain visible and can be retried; no local Decision
  fixture or synthetic success is used.
- Commands require a rationale and submit the Decision's `expectedVersion`.
- A version conflict refreshes the inbox so the operator must decide again
  against current state.
- Mission failure requires explicit confirmation. Controls remain disabled
  while a command is in flight.
- The API enforces human-only access and workspace authorization; the frontend
  does not infer or replace those permissions.
