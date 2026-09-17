# ADR-0092: Desktop Release Manifest and Windows CI

> Status: accepted  
> Owner: desktop maintainers  
> Date: 2026-08-22  
> Scope: `desktop/release-manifest.ps1`, `desktop/package-windows.ps1`, Windows CI

## Context

Desktop artifacts need reproducible identity and verifiable provenance. Local
portable builds are useful, but release review must be able to identify the
product version, target, source commit, generation time, size, and SHA-256 of
the application and sidecar without inspecting credentials or runtime state.

## Decision

Generate a JSON release manifest beside every verified desktop package. The
manifest records the Tauri product name and version, Windows target triple,
source commit, UTC generation timestamp, and hashes for the application,
sidecar, portable ZIP, and any generated installers. A Windows GitHub Actions
workflow pins Rust 1.88.0 and Tauri CLI 2.11.4, runs the existing packaging and
smoke gates, and uploads the verified artifacts and manifest.

The workflow does not sign artifacts yet. Signing, update metadata, and rollback
are separate release controls and must be added before public distribution.

## Consequences

- Every CI package has a machine-readable identity and integrity record.
- Portable packaging remains available when installer backends are unavailable.
- CI failures distinguish compilation, packaging preflight, and runtime smoke.
- No credentials or business state are copied into release metadata.

## Verification

- `package-windows.ps1 -Portable` generates the manifest after artifact and
  runtime smoke pass.
- The workflow uploads only the verified executable, sidecar, portable ZIP,
  installers when present, and the release manifest.
