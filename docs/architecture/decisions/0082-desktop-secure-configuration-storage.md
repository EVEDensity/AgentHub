# ADR-0082: Desktop Secure Configuration Storage

> Status: accepted
> Owner: desktop maintainers
> Date: 2026-08-22
> Scope: `desktop/src-tauri/src/config.rs`, desktop configuration commands

## Context

The desktop shell needs endpoint and Artifact-directory settings before it can
manage a local Runtime. Persisting access tokens beside those settings would
make ordinary configuration backups and diagnostics credential-bearing. A
desktop user also must not be asked to configure secrets through environment
variables or Docker files.

## Decision

`ConfigurationStore` separates non-sensitive configuration from credentials.
The JSON configuration file stores only validated HTTP(S) endpoints, an
absolute local Artifact directory, and schema version `1`. URL credentials,
query strings, fragments, unsupported schemes, and relative Artifact paths are
rejected.

Mission Control tokens, MCP tokens, and model API keys are stored using the
platform credential store through `keyring`. The Windows build uses Windows
Credential Manager. The status API exposes only presence states and never
returns a secret. Secret set and clear operations are separate from config-file
writes, so a config save cannot accidentally serialize a token.

Unsupported operating systems fail closed at compile time until a native
credential-store adapter is provided; the product must not silently fall back
to plaintext or an insecure mock store.

## Consequences

- Configuration backups do not contain model or control-plane credentials.
- Runtime code can retrieve a secret through the native boundary without
  exposing it to the UI or process environment.
- Cross-platform desktop builds require an explicit secure-store adapter before
  they are enabled.
- File and credential-store updates are separate operations; onboarding must
  surface a clear retry state if either operation fails.

## Verification

- Native tests prove secret values are absent from `config.json`.
- Tests reject URL credentials and unsupported endpoint schemes.
- Clearing a missing or existing secret is idempotent and status remains
  redacted.
