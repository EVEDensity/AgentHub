# ADR-0103: Single-Entry Desktop Orchestration

> Status: accepted
> Owner: desktop maintainers
> Date: 2026-08-25
> Scope: `desktop/`, packaged local service lifecycle, Windows delivery

## Context

The current Windows package provides a Tauri shell and a Runtime sidecar. It
still exposes a configuration boundary because Mission Control and the server
services are deployed separately. That is appropriate for a server or private
deployment, but it is too demanding for an ordinary desktop user: downloading
the application must not imply installing Docker, starting Compose, finding
ports, or entering internal service credentials.

The product needs one obvious action while preserving the existing ownership
boundaries. Mission Control remains the source of business truth. The desktop
must not create a second Mission store or claim work succeeded because a local
process is alive.

## Decision

The desktop product will expose one user-facing entry point: `AgentHub.exe`.
The executable owns local service orchestration and the UI. Internal services
may remain separate executables, but they are bundled as private resources,
started and stopped by the desktop, health-checked, and never documented as
manual user actions.

The target package shape is:

```text
AgentHub/
  AgentHub.exe
  resources/        # bundled internal services; implementation detail
  data/             # per-user database and artifacts
  config/           # non-secret defaults and version metadata
```

On first launch the desktop creates the per-user data directory, initializes
the local database and Artifact directory, starts the required local services,
waits for their readiness contracts, starts the Runtime sidecar, and opens the
UI. A bounded supervisor records child PIDs, captures redacted diagnostics,
reaps children on shutdown, and reports explicit failure when a service is not
ready.

The first local mode should bundle only the smallest useful core: Gateway,
Mission Control, MCP Gateway, Runtime, and SQLite-backed local state. Redis,
NATS, Qdrant, observability, GPU model servers, and other deployment-scale
components remain server/private-deployment concerns until a measured desktop
use case requires them.

Remote endpoints and credentials remain an advanced mode. Secrets must be
entered by the user or obtained through an explicit login/device authorization
flow and stored in the OS credential store. No token, API key, or private
deployment credential may be embedded in a download artifact.

## Consequences

- A normal user installs or extracts the package and starts `AgentHub.exe`.
- Internal service boundaries remain available for independent testing and
  future server deployment; single-entry UX does not require a risky rewrite
  into one process.
- The desktop package grows and needs process supervision, health contracts,
  upgrade rollback, and clean-machine testing.
- SQLite/local mode must be explicitly bounded so it does not become a second
  source of Mission truth when connected to Mission Control.
- Docker Compose remains supported for server and private deployments and is
  removed from the ordinary desktop onboarding path.

## Alternatives

- **Keep endpoint/token onboarding as the default:** rejected because it makes
  deployment infrastructure a prerequisite for ordinary users.
- **Merge every service into one process immediately:** rejected because it is
  a high-risk rewrite with no UX benefit over hidden child processes.
- **Package the entire Compose topology:** rejected because it adds database,
  queue, vector, observability, and model-server cost to every desktop install.

## Implementation Gates

1. Preserve current shell, sidecar, and contract tests.
2. Add the orchestrator and bundled-service resource layout.
3. Add local SQLite initialization and migration tests.
4. Add startup, crash, shutdown, upgrade, and rollback smoke tests.
5. Produce Portable and MSI artifacts with `AgentHub.exe` as the only
   documented user action.
6. Validate on a clean Windows host without Docker, Go, Rust, or the repo.

Until these gates pass, the current package must be described as a desktop
shell plus Runtime sidecar, not as a self-contained local AgentHub platform.
