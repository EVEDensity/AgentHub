# ADR-0094: Management Mission Control Projection

> Status: implemented  
> Owner: frontend and Mission Control maintainers  
> Date: 2026-08-22  
> Scope: `frontend/components/admin/MissionControlPanel.tsx`

## Context

The desktop launcher deliberately opens the management backend instead of
duplicating Mission state. The backend already exposes durable Mission,
WorkUnit, Artifact, Evidence, and Decision commands, but the admin shell only
surfaced the Decision inbox. Operators therefore could not inspect the primary
execution chain from one real control-plane view.

## Decision

Add a Mission Control admin projection backed exclusively by `/api/v1/missions`
endpoints. It lists Missions in the selected workspace, creates a Mission by
passing a user-supplied Contract JSON through backend validation, starts or
cancels a selected Mission, and reads its WorkUnits, Artifacts, and Evidence.
Decision resolution remains in the existing versioned Decision inbox.

The frontend owns loading, selection, error, and retry presentation only. It
does not synthesize Mission, WorkUnit, Artifact, Evidence, lease, or status
data when the backend is unavailable. Contract semantics remain owned by the
Python domain schema and service.

## Consequences

- Desktop remains a launcher and the admin backend becomes the minimum business
  workflow surface.
- Operators can see execution and verification evidence without a second data
  model.
- Rich Contract authoring and Artifact byte viewing remain follow-up work; this
  stage intentionally uses backend-validated JSON and metadata-only lists.

## Verification

- Frontend TypeScript compilation and Next production build pass.
- Existing Decision inbox tests pass.
- Mission Control requests use workspace-scoped API paths and render honest
  error states for unavailable backend responses.
