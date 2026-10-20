# AgentHub Desktop

> Status: implemented source integration and limited packaging checks;
> packaged shared UI and native WebView2 acceptance remain target.
> Owner: desktop maintainers
> Last reviewed: 2026-10-18

`desktop/` is the native, user-facing entry point for AgentHub. It owns local
Runtime lifecycle, redacted diagnostics, OS integration, and secure credential
storage. It does not own Mission, Contract, WorkUnit, Artifact, Evidence,
Decision, or Outcome state.

## Product Boundary

The Tauri shell is the user's single entry point, not a second Mission
dashboard. Missions, approvals, artifacts, evidence, and other business
workflows remain owned by Mission Control. The current release packages the
desktop shell and Runtime sidecar; it does not package the server deployment.

The full local-services profile targets one `AgentHub.exe`: it owns the lifecycle of bundled
Mission Control, Gateway, MCP Gateway, and the standalone Next.js admin
frontend, plus the Runtime sidecar. It creates local data directories, starts
health-checked services, and opens the admin UI without requiring Node, Go,
Rust, Python, or Docker. Docker Compose remains the server/private-deployment
path. The bundled admin frontend bakes the local stack endpoints
(`127.0.0.1:28000` control plane, `:28001` gateway) into its Next.js rewrites
at `local-services/build-windows.ps1` build time; standalone Next.js bundles
cannot re-read those endpoints from runtime environment variables. The build
pins `MISSION_CONTROL_URL` to the local control plane even when a remote override
is inherited, then restores that environment value afterward. The first
desktop instance receives the 28000 port group by allocation order;
concurrent second instances get their own groups while the bundled frontend
still targets the first instance (single-instance limitation).
`package-windows.ps1 -LocalServices` explicitly rebuilds this full profile from
a clean committed checkout. PR/main CI and the distribution workflow select it;
the tag-only `publish-stack` job builds a separate downloadable stack. Actual
Portable and MSI extraction verifies shared resource inclusion for the full
profile. Shell-only packages retain their narrower scope. See
[ADR-0133](../docs/architecture/decisions/0133-desktop-package-resource-integrity.md).

The admin Terminal now displays an unavailable notice without command input;
it has no real terminal service and shows no simulated system or execution
results. This applies to the shared admin view opened by Desktop as well.

The shared Next.js Mission panel now contains the UI-1 shadcn/Radix Dialog and
local Monaco code preview, documented in
[ADR-0128](../docs/architecture/decisions/0128-registered-artifact-text-preview.md).
The existing public-asset copy carries its generated editor, worker and license
files into the standalone bundle. This source integration does not establish a
new packaged WebView2 verification record. The same shared source now includes
the UI-2a assistant-ui Mission dialog, replacing the admin public-gateway chat
and simulated typing with admission, bounded observations and real cancellation.
Closing pauses observation and leaves accepted server work running; no native
session/permission/model authority is added. See
[ADR-0129](../docs/architecture/decisions/0129-mission-assistant-ui-projection.md).
This is also source/browser integration, not a new packaged release record.
The same Next bundle's main route now uses UI-2b native Session/Mission
observations and Mission Control's personal-scope probe, documented in
[ADR-0130](../docs/architecture/decisions/0130-native-session-mission-observation.md).
It restores selectable task goals and bounded observations, not original full
transcripts or execution checkpoints. This remains source/browser evidence;
the Desktop default admin entry and native shell are unchanged. Packaged
validation follows the
[UI component plan](../docs/roadmaps/composable-agent-platform-plan.md#511-web-与-desktop-ui-组件target).
The shared Mission Artifact surface also includes UI-3a's local xterm 6.0.0
registered-log snapshot renderer and shipped MIT notices. It verifies original
bytes, displays bounded control-neutralized text and adds no native command or
terminal transport. The sidebar shows only actual frontend navigation activity.
See [ADR-0131](../docs/architecture/decisions/0131-registered-log-artifact-snapshots.md).
This does not establish a new packaged Desktop/WebView2/offline release result.
UI-3b removes the shared admin header's configured-Agent/fallback-model label
and fixed token/context percentages. It states that those execution metrics
are not connected, while retaining workspace/user/navigation context. This is
the same shared source/browser surface, not native WebView2 or packaged proof.
The
dependency-free `ui/` shell keeps bootstrap, service lifecycle, diagnostics and
native credential responsibilities; it does not gain another Mission store or
an independent Agent runtime. Component adoption must pass packaged WebView2
and offline asset checks as well as browser tests before Desktop support is
claimed. This change does not modify the Tauri shell or its command surface.

## Layout

- `src-tauri/`: Tauri application, local process boundary, and native tests.
- `runtime-sidecar/`: standalone Rust bootstrap process and packaging staging
  script. It owns only local readiness, not Mission execution.
- `package-windows.ps1`: one-command Windows build and installer gate.
- `packaged-artifact-smoke.ps1`: verifies the built application and sidecar
  digest without starting the desktop process.
- `package-resource-manifest.mjs`: inventories the fresh full-service stage,
  checks required UI assets and verifies extracted bytes against the external
  manifest. `packaged-resources-smoke.ps1` extracts actual Portable/MSI files;
  `require-package-resource-reports.ps1` independently requires both reports.
- `packaged-runtime-smoke.ps1`: starts only the packaged sidecar and verifies
  its loopback readiness contract.
- `installer-install-smoke.ps1`: installs the generated MSI/NSIS package,
  validates shortcuts and sidecar placement, starts the shell, and uninstalls
  on an isolated Windows runner.
- `shell-browser-smoke.ps1` / `.mjs`: runs the static shell in the locked
  Playwright Chromium, blocks non-static requests, and writes three actual
  screenshots plus a classified report under a unique `output/playwright/`
  run directory. Its native commands use the existing browser-preview fixtures.
- `webview2-gui-smoke.ps1`: compatibility entry point for that browser preview;
  the historical name does not establish native WebView2 coverage.
- `webview2-native-smoke.mjs`: launches a digest-matched packaged executable
  and records whether its own WebView2 process appears. Passing this check
  does not verify login, shared UI, services, input, zoom, or shutdown.
- `updater-rollback-smoke.ps1`: verifies signed updater metadata and rehearses
  restoring the previous ready sidecar after a failed candidate launch.
- `ui/`: dependency-free shell UI served by the desktop application,
  including the first-run stack bootstrap wizard (below).
- `src-tauri/src/bootstrap.rs`: stack bootstrap downloader (north-star
  M3). On first run the shell can fetch a runtime stack from a release
  source (`stack-manifest.json`, schemaVersion 1) into
  `%LOCALAPPDATA%\AgentHub\stacks\<version>-<commit>\` with per-file
  sha256 verification, `*.part` staging, resume, and an atomic
  `.pinned` switch — a failed install never disturbs the current pin.
  The Tauri command `bootstrap_stack` runs it on the async runtime
  pool and streams progress over the `bootstrap-progress` channel.
  The same manifest format is produced by
  `scripts/make_stack_manifest.py` and consumed by the developer CLI
  (`agenthub upgrade`), so desktop and CLI bootstrap identically.

## First-run bootstrap wizard (M3)

When no stack is pinned yet, the shell opens the first-run wizard
(`ui/index.html` `#bootstrap-dialog`, wired in `ui/main.js`). The user
confirms or edits the release-source manifest URL, then the wizard:

- streams `bootstrap-progress` events into a per-file log and progress
  bar (file index, total, verified/downloaded counts, resume flag);
- shows per-file sha256 verification as it happens and only switches
  the pin after every file verifies;
- on failure, keeps already-verified files so "重试" resumes instead of
  restarting, and never disturbs an existing pinned stack;
- remembers the manifest URL and a dismissal choice in localStorage;
  the wizard also stays reachable from Settings for later upgrades.

The wizard reuses the same `bootstrap_stack` Tauri command and
`stack-manifest.json` contract as the CLI bootstrap path. Structural
UI tests live in `tests/desktop/test_bootstrap_wizard_ui.py`.

The `desktop-v*` release pipeline (`.github/workflows/desktop-windows.yml`
`publish-stack` job) builds the full local-services stack
(mission-control, gateway, mcp-gateway, frontend, runner sidecar) and
attaches it with the manifest to the GitHub Release, so a clean machine
can install the complete local SQLite profile without preinstalled
PG/Docker.

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

The Windows workflow also runs the shell browser preview after packaging. It
checks the home, settings and monitoring DOM and captures actual screenshots.
It uses the version already installed from `frontend/package-lock.json`, with
no dynamic CLI download. From a development checkout, run `npm ci` in
`frontend/`, install its matching Chromium with
`node frontend/node_modules/@playwright/test/cli.js install chromium` from the
repository root, then run `desktop/shell-browser-smoke.ps1 -Port 0`.
Missing tools/browser, occupied ports, assertion and cleanup errors fail.
Reports explicitly set native WebView2, packaged application and real-service
verification to false. The installed-app startup check detects immediate
process failure; it does not inspect WebView2 business flows.

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
binary is not bundled. Installer process startup and browser-preview checks
cover different boundaries; full shared UI acceptance remains a native gate.

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
   checkout installed. — target. The CI runner has developer tools installed;
   its installer smoke detects immediate startup failures and does not prove
   a complete task flow on a machine without those dependencies.

The historical workflow run 33231676165 remains a packaging/startup record,
not current native WebView2 or shared UI acceptance. Do not infer full-stack
inclusion from a shell-only package or GA readiness from these smoke names.
The next delivery slices are:

1. **UI-4a, browser-preview evidence:** fixed local tools, owned test resources,
   classified reports and actual screenshots; no native/package/service claim.
2. **UI-4b, packaged resources:** inspect the actual selected Portable
   and installer contents from the same commit, including the full stack,
   baked loopback rewrites, Monaco workers, xterm, fonts and license notices.
   Missing/stale/tampered assets must fail before any native GUI claim.
3. **UI-4c, native WebView2 (in progress):** `webview2-native-smoke.mjs`
   launches a digest-matched `agenthub-desktop.exe` and accepts only an
   `msedgewebview2.exe` parent chain that reaches that process. A passing
   report is process identity only: login, shared Mission/Artifact frames,
   local assets, Chinese input, zoom, and shutdown stay false, and no
   debugging port is opened. See
   [ADR-0134](../docs/architecture/decisions/0134-native-webview2-process-identity.md).
   The shell's in-memory token is not an admin-origin login bridge;
   injecting fixture localStorage cannot establish product authentication.

See the [ordered component plan](../docs/roadmaps/composable-agent-platform-plan.md#511-web-与-desktop-ui-组件target)
for owners, prerequisites and failure gates. Signing and clean-machine
installation/upgrade/rollback remain additional release gates.

## Full-service resource verification

From a clean committed Windows checkout with the locked frontend dependencies,
Python application dependencies and PyInstaller 6.16.0, Go, Node and the pinned
Rust/Tauri tools installed:

```powershell
.\desktop\package-windows.ps1 -TargetTriple x86_64-pc-windows-msvc -LocalServices
.\desktop\require-package-resource-reports.ps1 -Directory desktop/.runtime/package-resources -SourceCommit (git rev-parse HEAD)
```

This invocation rebuilds the services and frontend once, produces both the
installer and Portable ZIP, and verifies actual ZIP/MSI extraction against an
external file inventory from the same build. It does not install or launch the
UI for this resource check. Missing or extra files, wrong source commit,
modified bytes, broken rewrites, absent workers/fonts and incorrect selected
component notices fail. Build input hashes, full commit, artifact hashes and
classified reports are retained in ignored `.runtime/package-resources/`.
The PR/main CI gate requires both package forms from its exact selected SHA.

`-LocalServices -Portable` verifies only the Portable form; `-NoInstaller`
builds the release application without claiming installer resource acceptance.
Omitting `-LocalServices` retains the shell-only developer profile. An old
staged Mission Control executable is never reused by the full-service build.
Next output is fixed to `.next`; inherited endpoint/output environment values
are restored. See [local build ownership](local-services/README.md).
The two tracked PDF runtime mirrors retain their upstream bytes through
`.gitattributes`; Windows checkout must not rewrite them before npm stages the
same locked files. The clean-source check continues to reject real changes.

The inventory proves the selected package bytes and UI component notices. It
is not a signed build attestation or a complete dependency-license audit.
Native WebView2, real login/Mission flow, public signing and clean-machine
operation remain separate acceptance gates.

## Configuration and credentials

The task form uses `ui/default-contract.js`, a projection of the shared Python
default in `app/services/default_mission_contract.py`. The cross-language
contract test executes this projection and checks its exact protocol fields.
Default completion means independent verification of registered output
availability, size and SHA-256. It does not certify code correctness or actual
test execution, and the default contract grants no tools. The existing Runner
output kind `test-result` can contain model text; no minimum byte size is
imposed by `artifact-set.v1`.

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
it in the system browser through a native command. The shell also embeds the
shared Next.js admin view in the full local-services profile. Those workflows
remain server-owned; embedding the view does not create another business store.

## Runtime sidecar contract

The native layer exposes a versioned `RuntimeSnapshot` contract. `status`
describes process lifecycle, while `readiness` describes whether the sidecar
has passed its health probe. These are intentionally separate: an alive process
is not evidence that the Runtime is ready to execute work. A terminated child
reports its exit code and an unhealthy readiness state. The contract contains
no Mission or WorkUnit data and is safe to evolve independently from the
control-plane API.
