# AgentHub Desktop

> Status: minimum desktop shell
> Owner: desktop maintainers
> Last reviewed: 2026-08-24

`desktop/` is the native, user-facing entry point for AgentHub. It owns local
Runtime lifecycle, redacted diagnostics, OS integration, and secure credential
storage. It does not own Mission, Contract, WorkUnit, Artifact, Evidence,
Decision, or Outcome state.

## First vertical slice

The Tauri shell is a launcher, not a second Mission dashboard. Its first view
shows the Mission Control entry point, local Runtime status, and concise
connection feedback. Missions, approvals, artifacts, evidence, and other
business workflows remain in the management backend. A start request fails
explicitly until the configured Runtime sidecar is available; it never starts
Docker, a mock provider, or an unconfigured Runner. Connection onboarding is
available from the launcher settings dialog and keeps credentials in the OS
credential store.

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
- `updater-rollback-smoke.ps1`: verifies signed updater artifacts and restores
  the previous ready sidecar after a failed candidate launch.
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

The initial shell intentionally has no Docker dependency. It is a lifecycle
surface, not a replacement control plane. Once configuration is ready, the
native layer may start only the packaged `agenthub-runtime.exe` sidecar from
the application resource directory; a missing sidecar fails explicitly. The
bootstrap sidecar must expose `http://127.0.0.1:18097/readyz` and return the
current runtime protocol version before the desktop reports it ready. It is a
lifecycle process, not the Python Mission Runner.

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
