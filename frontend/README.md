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
