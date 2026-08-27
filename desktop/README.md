# AgentHub Desktop

> Status: implemented shell; single-entry local orchestration planned
> Owner: desktop maintainers
> Last reviewed: 2026-08-24

`desktop/` is the native, user-facing entry point for AgentHub. It owns local
Runtime lifecycle, redacted diagnostics, OS integration, and secure credential
storage. It does not own Mission, Contract, WorkUnit, Artifact, Evidence,
Decision, or Outcome state.

## Product Boundary

The Tauri shell is the user's single entry point, not a second Mission
dashboard. Missions, approvals, artifacts, evidence, and other business
workflows remain owned by Mission Control. The current release packages the
desktop shell and Runtime sidecar; it does not package the server deployment.

The desktop experience is one `AgentHub.exe`: it owns the lifecycle of bundled
Mission Control, Gateway, MCP Gateway, and the standalone Next.js admin
frontend, plus the Runtime sidecar. It creates local data directories, starts
health-checked services, and opens the admin UI without requiring Node, Go,
Rust, Python, or Docker. Docker Compose remains the server/private-deployment
path. The bundled admin frontend bakes the local stack endpoints
(`127.0.0.1:28000` control plane, `:28001` gateway) into its Next.js rewrites
at `local-services/build-windows.ps1` build time; standalone Next.js bundles
cannot re-read those endpoints from runtime environment variables. The first
desktop instance receives the 28000 port group by allocation order;
concurrent second instances get their own groups while the bundled frontend
still targets the first instance (single-instance limitation).

## Layout

- `src-tauri/`: Tauri application, local process boundary, and native tests.
- `runtime-sidecar/`: standalone Rust bootstrap process and packaging staging
  script. It owns only local readiness, not Mission execution.
- `package-windows.ps1`: one-command Windows build and installer gate.
- `packaged-artifact-smoke.ps1`: verifies the built application and sidecar
  digest without starting the desktop process.
- `packaged-runtime-smoke.ps1`: starts only the packaged sidecar and verifies
  its loopback readiness contract.
- `installer-install-smoke.ps1`: installs the generated MSI/NSIS package,
  validates shortcuts and sidecar placement, starts the shell, and uninstalls
  on an isolated Windows runner.
- `webview2-gui-smoke.ps1`: runs the CLI-first shell regression and writes
  screenshots under `output/playwright/`.
- `updater-rollback-smoke.ps1`: verifies signed updater metadata and rehearses
  restoring the previous ready sidecar after a failed candidate launch.
- `ui/`: dependency-free shell UI served by the desktop application.

## Development

The desktop shell uses Rust 1.88, fixed in `rust-toolchain.toml`; it is
intentionally independent from the server Rust workspace. Install the Tauri
CLI outside the product runtime, then run from this directory:

```powershell
cargo install tauri-cli --version "^2"
cargo tauri dev --manifest-path src-tauri/Cargo.toml
```

Before a Tauri bundle or dev session that enables the external binary, stage
the sidecar for the current Windows target:

```powershell
.\runtime-sidecar\build-windows.ps1
.\runtime-sidecar\packaging-preflight.ps1
```

The preflight parses `tauri.conf.json` and fails closed when the configured
sidecar name, target-specific staged executable, or file type is incorrect. It
does not start the application or any server dependency. A full bundle still
requires the Tauri CLI; the preflight is the local packaging gate when that
optional developer tool is unavailable.

For a complete Windows package, use the repository script. It builds the
target-specific sidecar, runs the same preflight, verifies the Tauri CLI, and
only then invokes the bundle command:

```powershell
.\package-windows.ps1
```

The command fails before bundling when the Tauri CLI is unavailable or the
sidecar does not match the host target. Installing the CLI is a developer or
CI prerequisite; it is not a product runtime dependency.

When installer backends are unavailable on a development host, use
`-NoInstaller` to build and verify the release application and packaged
sidecar without claiming an MSI or NSIS installer:

```powershell
.\package-windows.ps1 -NoInstaller
```

To create a portable ZIP containing the verified desktop executable and
sidecar, use:

```powershell
.\package-windows.ps1 -Portable
```

Portable mode is not an MSI/NSIS installer and still requires WebView2 on the
target Windows machine.

After a successful Tauri build, the artifact smoke verifies that the release
application exists and that the bundled `agenthub-runtime.exe` has the same
SHA-256 as the staged target sidecar. It does not claim that an installer was
created when WiX or NSIS cannot run on the build host.

The runtime smoke then starts the bundled sidecar with the fixed readiness
endpoint, validates protocol version `1`, `ready`, and—when an Artifact root is
supplied—`artifactRootStatus: ready`, then terminates that test process. It
does not start the desktop GUI, Docker, or any server service.

The packaging command prints the absolute release application, sidecar, and
any generated MSI/NSIS installer paths when it finishes.

Each successful package also writes `bundle/AgentHub-<target>-release.json`.
This release manifest records the product version, target, source commit, UTC
generation time, file sizes, and SHA-256 digests for the verified application,
sidecar, portable ZIP, and any installers. It contains no credentials or
Mission state. The Windows CI workflow runs the installer and portable gates
and uploads the artifacts with this manifest.

The Windows workflow also runs the GUI smoke after packaging. It verifies first
render, the connection settings dialog, and screenshot capture. Native
WebView2 availability remains covered by the installed-app startup check in
`installer-install-smoke.ps1`.

Signed updater artifacts are generated only for `desktop-v*` tags when
`AGENTHUB_UPDATE_ENABLED=1`, a public key, endpoint, and
`TAURI_SIGNING_PRIVATE_KEY` are supplied by CI secrets. Manual builds remain
unsigned internal artifacts. The updater plugin is included in the native
shell and its generated permissions are tracked under `src-tauri/gen/`.

Formal installer mode also runs `installer-artifact-smoke.ps1`. It fails when
the bundle contains no non-empty MSI/NSIS installer or when installer digests
do not match the release manifest. This gate validates artifact presence and
integrity. On the disposable Windows CI runner, `AGENTHUB_INSTALLER_LIFECYCLE_SMOKE=1`
also runs `installer-install-smoke.ps1`: it installs the preferred MSI (or the
NSIS fallback), verifies the installed executable and sidecar, resolves an OS
shortcut, detects immediate GUI/WebView2 startup failure, and uninstalls through
the registered Windows command. This mutating check is opt-in locally and is
enabled by the Windows workflow.

The current shell has no Docker dependency. It owns local Runtime and local
service lifecycle plus redacted diagnostics. On first launch it creates local
defaults and an Artifact directory; remote endpoints and credentials remain
optional advanced configuration. `ServiceSupervisor` starts the bundled
Mission Control, Gateway, and MCP Gateway binaries with per-service health
endpoints, bounded restart (3 attempts), and fail-closed Missing status when a
binary is not bundled. End-to-end desktop startup is verified by the Windows
workflow GUI/installer smokes on a clean runner.

## Delivery Plan

1. Keep the current shell and sidecar contracts green as the release baseline.
   — implemented; packaging smokes enforce it.
2. Native service orchestrator that starts bundled Gateway, Mission Control,
   and MCP components as hidden child processes. — implemented
   (`src-tauri/src/services.rs`).
3. Embedded SQLite database and per-user Artifact directory for local mode;
   server PostgreSQL/Compose unchanged. — implemented via
   `AGENTHUB_DB_BACKEND=sqlite` wiring in the supervisor.
4. Bounded startup, health checks, shutdown, crash diagnostics, and
   versioned resource directories for upgrade and rollback. — implemented
   (health checks, restart bound, supervisor reaps on stop/drop; each bundled
   stack is snapshotted once per `version-commit` under
   `%LOCALAPPDATA%\AgentHub\stacks`, and when a bundled service binary is
   missing the supervisor falls back to the newest persisted copy that
   carries it — reported via `stack_info` as `bundled`, `persisted`, or
   `unversioned`; manual pinning of an older stack remains open).
5. Make `AgentHub.exe` the only documented user action in Portable and MSI
   packages; move endpoint and token fields to an advanced deployment mode.
   — implemented (advanced settings dialog exists; portable ZIP ships
   `START-HERE.txt`; MSI/NSIS installers ship `README-first.txt` at the
   install root next to the exe, verified by administrative MSI extraction).
6. Validate a clean Windows machine with no Docker, Go, Rust, or repository
   checkout installed. — enforced by `installer-install-smoke.ps1` on the
   disposable CI runner, not yet run from a physical clean machine record.

Items 4-6 remain the active R5-1 slice. The package now contains the shell,
Runtime sidecar, and the local service stack, so the admin UI is a
self-contained local deployment target.

## Configuration and credentials

Desktop configuration is split by sensitivity. The app configuration file
contains only validated Mission Control and MCP endpoints, the local Artifact
directory, and a schema version. Mission Control tokens, MCP tokens, and model
API keys are stored through the operating system credential store and are
never serialized into that file, returned by a Tauri command, placed in an
environment variable, or written to diagnostics. The current release targets
Windows Credential Manager first; unsupported targets fail closed until their
native credential-store adapter is implemented.

The native commands expose redacted configuration details, configuration
status, secret set/clear operations, and a Mission Control reachability probe.
Details return only validated endpoints and the Artifact path; status reports
only `configured`, `missing`, or `unavailable` for each credential and never
returns its value. Refresh probes `GET /api/health` and then `GET /api/auth/me` on the saved
origin with a two-second timeout, no redirects, and a bounded response. The
launcher reports `not_configured`, `unreachable`, `unauthorized`, `unhealthy`,
or `reachable` and never treats a saved URL as proof that Mission Control is
live. `reachable` requires both a valid health contract and an authenticated
session JSON with a non-empty `id`. When the endpoint is configured but the
Mission Control token is missing, the probe reports `unauthorized` without
calling `/api/auth/me`. The probe may attach the stored Mission Control token
only for HTTPS or loopback HTTP; the token and response body never leave the
native layer. The launcher
settings dialog writes ordinary configuration first and then non-empty secrets
independently, keeping the dialog open when either operation fails. Runtime
startup remains blocked until the configured sidecar is available. Opening
Mission Control in the browser stays available whenever an endpoint is saved,
even if the health probe currently fails.

The native supervisor reports `starting` while probing the loopback readiness
endpoint and only reports `ready` for HTTP 200 with the current protocol
version, a `ready` status, and—when an Artifact directory is configured—a
matching `artifactRootStatus` of `ready`. Process liveness alone is never
treated as readiness. When configuration includes an Artifact directory, the
supervisor passes it to the sidecar through the fixed `--artifact-root`
argument; the path is validated at sidecar startup and is not echoed in the
readiness JSON.
The supervisor reaps the sidecar on explicit stop and application teardown. It
fails closed when the loopback health port is occupied, and stops a child that
does not become ready within the bounded startup timeout. These diagnostics are
fixed, redacted lifecycle messages; sidecar output and credentials are never
returned to the UI.

When a validated Mission Control endpoint is configured, the desktop can open
it in the system browser through a native command. The desktop does not embed
or duplicate the management workflows.

## Runtime sidecar contract

The native layer exposes a versioned `RuntimeSnapshot` contract. `status`
describes process lifecycle, while `readiness` describes whether the sidecar
has passed its health probe. These are intentionally separate: an alive process
is not evidence that the Runtime is ready to execute work. A terminated child
reports its exit code and an unhealthy readiness state. The contract contains
no Mission or WorkUnit data and is safe to evolve independently from the
control-plane API.
