# ADR-0101: Desktop Authenticated Session Probe

> Status: implemented
> Owner: desktop maintainers
> Date: 2026-08-24
> Scope: `desktop/src-tauri/src/probe.rs`, launcher connection feedback

## Context

ADR-0100 added a Mission Control health probe against `GET /api/health`. That
endpoint is unauthenticated, so a stored but invalid Mission Control token
could still show `reachable`. Operators need the launcher to distinguish a live
origin from an authenticated workspace session.

The desktop shell already owns the OS credential store. The Runtime sidecar
must not receive Mission Control tokens or perform remote session checks.

## Decision

After a successful health probe, the desktop performs a second bounded probe
against the fixed path `GET /api/auth/me` when a Mission Control token is
configured.

The session probe is fail-closed:

- The path is fixed. Renderer input cannot choose another path.
- Redirects are not followed.
- The same two-second timeout and 8 KiB response cap apply.
- Authorization uses the stored Mission Control token with the same transport
  rules as ADR-0100.
- HTTP 401 or 403 is `unauthorized`.
- HTTP 200 requires JSON with a non-empty `id` field. Any other body is
  `unhealthy`.
- When the endpoint is configured but the token is missing, the launcher
  reports `unauthorized` without calling `/api/auth/me`.
- Transport failure after a healthy origin is `unreachable`.

`reachable` now means the configured origin is healthy and the stored token
represents a valid authenticated session. The probe still does not list
Missions, WorkUnits, or other business state.

## Consequences

- Invalid or missing credentials are visible before opening the browser.
- Health-only success is no longer presented as a connected workspace.
- `/api/auth/me` becomes a desktop launcher contract; breaking changes require
  an ADR update.
- The probe still does not validate workspace-scoped Mission APIs.

## Verification

- Native tests cover missing token, valid session JSON, rejected credentials,
  and unchanged secret redaction.
- ADR-0100 health-only tests are updated for the two-step contract.
