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
