# ADR-0089: Desktop Packaged Artifact Integrity

> Status: accepted  
> Owner: desktop maintainers  
> Date: 2026-08-22  
> Scope: `desktop/packaged-artifact-smoke.ps1`, Tauri release output

## Context

Tauri can compile the desktop application while an installer backend fails
later. The desktop must not claim a valid package unless the application and
the exact target sidecar are both present. Installer availability is also an
environment concern and should not obscure the stronger artifact invariant.

## Decision

After Tauri builds the release application, run a platform-specific artifact
smoke that requires a non-empty application executable and bundled sidecar.
The smoke compares the bundled sidecar SHA-256 with the target-staged sidecar
used by `externalBin`. It does not start the app, access Mission Control, or
execute a Mission.

Installer backends remain a separate gate. A WiX or NSIS failure is reported as
an installer failure even when the application and sidecar artifact smoke
passes.

## Consequences

- Sidecar replacement or target mismatches are detected before release.
- Build hosts can verify the core desktop artifact without claiming installer
  success.
- The smoke is deterministic and does not require Docker or server services.
- The packaging command can explicitly use `-NoInstaller` for this artifact
  gate; the default command still requires an installer backend.

## Verification

- Windows target artifact smoke compares the built sidecar digest with the
  staged digest.
- Full installer verification remains pending when the host cannot run WiX or
  NSIS.
