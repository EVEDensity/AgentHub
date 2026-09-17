# ADR-0091: Desktop Portable Package

> Status: accepted  
> Owner: desktop maintainers  
> Date: 2026-08-22  
> Scope: `desktop/package-windows.ps1`, Windows release output

## Context

The desktop application and sidecar can be built and verified even when the
host cannot run WiX or NSIS. A release artifact is still useful for internal
testing and controlled distribution, but it must not be mislabeled as an
installer.

## Decision

Support an explicit `-Portable` packaging mode. It builds without an installer,
runs the artifact and runtime smoke gates, and creates a ZIP containing only
the release `agenthub-desktop.exe` and its matching `agenthub-runtime.exe`.
The output is labeled portable and the command reports its absolute path.

The portable package does not add server dependencies, Docker, or a second
business state model. WebView2 remains a target-machine prerequisite.

## Consequences

- Desktop artifacts can be transferred and tested without MSI/NSIS.
- Portable mode cannot claim installation, shortcuts, registry integration, or
  automatic updates.
- Installer generation remains the default release gate for formal distribution.

## Verification

- Portable mode runs both packaged artifact and runtime readiness smoke tests.
- The resulting ZIP contains the desktop executable and sidecar only.
