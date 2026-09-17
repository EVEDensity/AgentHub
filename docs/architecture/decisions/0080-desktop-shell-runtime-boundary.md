# ADR-0080: Desktop Shell Runtime Boundary

> Status: accepted
> Owner: desktop maintainers
> Date: 2026-08-22
> Scope: `desktop/`, local Runtime lifecycle, desktop-to-control-plane boundary

## Context

The web application and Compose deployment are useful developer and
private-deployment surfaces, but they are not an acceptable installation or
startup path for desktop users. A desktop product must own its local lifecycle,
configuration guidance, and diagnostics without requiring users to operate
Docker or create service secrets manually.

Mission Control remains the durable owner of Mission, Contract, WorkUnit,
Artifact, Evidence, Decision, and Outcome state. A desktop shell must not
introduce a local queue, state replica, or alternate success path.

## Decision

Create a standalone Tauri desktop shell under `desktop/`. Its native layer owns
only process-local Runtime lifecycle and redacted diagnostics. The initial
surface is a static control view that can query status and request start/stop.
When required local Runtime configuration is absent, a start request returns an
explicit configuration-required result rather than launching Docker, a mock
provider, or a synthetic Runner.

The desktop shell is not a member of the server Rust workspace. It is a
platform-specific application and releases on its own cadence. Future phases
will add OS credential storage, a bundled Runtime sidecar, onboarding, and the
existing Next.js workflow surface behind this boundary.

The shell pins Rust 1.88 locally because its desktop dependency graph includes
Rust 2024 manifests and current Unicode dependencies. This does not upgrade or
constrain the server workspace.

## Consequences

- End users receive a native entry point that does not expose Compose commands.
- Local lifecycle diagnostics remain separate from business truth.
- The first shell cannot execute work until a secure onboarding and sidecar
  configuration path exists; this is intentional fail-closed behavior.
- Desktop builds add a platform-specific Rust dependency graph and release
  pipeline.

## Alternatives considered

- Require Docker Compose in the desktop user flow: rejected because it exposes
  deployment infrastructure and credentials to ordinary users.
- Add local Mission persistence in the shell: rejected because it duplicates
  Mission Control truth and weakens recovery semantics.
- Continue with a browser-only application: rejected because it cannot own
  installation, local process lifecycle, and OS-native credential integration.

## Verification

- Native unit tests prove Runtime state does not report running before a child
  process exists and that missing configuration fails explicitly.
- The desktop UI renders the native status and configuration-required state.
- No desktop source references Docker or writes Mission Control domain state.

## Supersedes

None.
