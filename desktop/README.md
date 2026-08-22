# AgentHub Desktop

> Status: minimum desktop shell
> Owner: desktop maintainers
> Last reviewed: 2026-08-22

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

After a successful Tauri build, the artifact smoke verifies that the release
application exists and that the bundled `agenthub-runtime.exe` has the same
SHA-256 as the staged target sidecar. It does not claim that an installer was
created when WiX or NSIS cannot run on the build host.

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
status, and secret set/clear operations. Details return only validated
endpoints and the Artifact path; status reports only `configured`, `missing`,
or `unavailable` for each credential and never returns its value. The launcher
settings dialog writes ordinary configuration first and then non-empty secrets
independently, keeping the dialog open when either operation fails. Runtime
startup remains blocked until the configured sidecar is available. The native
supervisor reports `starting` while probing the loopback readiness endpoint and
only reports `ready` for HTTP 200 with the current protocol version and a
`ready` status. Process liveness alone is never treated as readiness.

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
