# ADR-0098: Windows Installer Lifecycle Smoke

> Status: implemented  
> Owner: desktop maintainers  
> Date: 2026-08-23

## Context

Artifact hashes prove that an MSI or NSIS executable was produced, but they do
not prove that Windows can install the product, resolve the packaged sidecar,
create an OS shortcut, start the WebView2 shell, or remove the product.

## Decision

The Windows packaging workflow opts into `installer-install-smoke.ps1` on an
isolated `windows-latest` runner. The smoke prefers MSI and falls back to NSIS,
installs silently, discovers the installed location from the uninstall
registry, verifies `agenthub-desktop.exe` and `agenthub-runtime.exe`, resolves a
Start Menu or Desktop shortcut target, starts the installed GUI long enough to
detect immediate WebView2/native startup failure, and uninstalls through the
registered command. It fails if the executable, sidecar, shortcut, or uninstall
entry remains.

The local packaging command leaves this lifecycle step opt-in through
`AGENTHUB_INSTALLER_LIFECYCLE_SMOKE=1`, because installation mutates the host
and requires a disposable Windows environment. Failure logs are kept by the CI
job and no credentials are used.

## Consequences

- CI now validates the real Windows install boundary in addition to artifact
  integrity.
- The GUI check is intentionally a startup smoke, not a substitute for the
  Playwright/WebView2 interaction suite.
- Developer machines remain safe by default; lifecycle validation is explicit.
