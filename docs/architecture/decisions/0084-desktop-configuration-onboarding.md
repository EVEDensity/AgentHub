# ADR-0084: Desktop Configuration Onboarding

> Status: implemented  
> Owner: desktop maintainers  
> Date: 2026-08-22  
> Scope: `desktop/ui/`, `desktop/src-tauri/src/config.rs`, desktop commands

## Context

The launcher must be usable without asking users to edit JSON files, environment
variables, or Docker settings. It also must not expose existing credentials in
the UI. The desktop shell is only a configuration and lifecycle surface;
Mission and Runtime business state remain owned by their existing services.

## Decision

The desktop provides an on-demand configuration dialog. A native
`configuration_details` command returns validated non-sensitive endpoints and
Artifact directory plus redacted credential availability. Secret values are
never returned. The form always clears secret inputs; an empty secret means
keep the existing credential. Saving writes ordinary configuration first and
then sends each non-empty secret to the OS credential store. If any operation
fails, the dialog stays open and reports a retryable error.

## Consequences

- First-run setup is possible from the desktop without Docker or manual files.
- Existing credentials cannot be recovered through the renderer.
- A partial save is visible and retryable; it is not presented as a completed
  onboarding flow.
- Runtime start remains governed by the existing readiness and configuration
  checks.

## Verification

- Native tests continue to prove redacted storage behavior.
- JavaScript syntax and Rust formatting, lint, and tests are required for each
  change.
