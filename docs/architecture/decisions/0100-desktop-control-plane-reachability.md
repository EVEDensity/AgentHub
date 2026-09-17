# ADR-0100: Desktop Mission Control Reachability Probe

> Status: implemented
> Owner: desktop maintainers
> Date: 2026-08-24
> Scope: `desktop/src-tauri/src/probe.rs`, launcher connection feedback

## Context

The desktop launcher already stores a validated Mission Control endpoint and
can open it in the system browser. ADR-0083 requires connection feedback, but
the shell treated a saved URL as a ready workspace. That claim is false: the
address may be down, reject credentials, or not speak the Mission Control
health contract.

The local Runtime sidecar remains a loopback lifecycle process. It must not
receive Mission Control tokens or become a second control-plane client.
Reachability therefore belongs in the native desktop layer, which already owns
the OS credential store.

## Decision

The desktop exposes a native `probe_control_plane` command. It reads the saved
Mission Control origin from configuration and, when present, issues a bounded
`GET /api/health` against that origin.

The probe is fail-closed:

- The path is fixed to `/api/health`. Renderer input cannot choose another
  path, host, or query string.
- Redirects are not followed.
- The request times out in two seconds and reads at most 8 KiB.
- A `reachable` result requires HTTP 200 and JSON `{"status":"ok"}` (also
  accepting `ready` or `healthy`). Any other body, including HTML, is
  `unhealthy`.
- HTTP 401 or 403 is `unauthorized`. Transport failure is `unreachable`.
- Missing configuration is `not_configured` and performs no network I/O.
- Authorization is attached from the OS credential store only for HTTPS or
  loopback HTTP. The token is never returned, logged, or copied into the
  snapshot.

The launcher shows these states instead of “configured means ready”. Opening
Mission Control in the browser remains allowed whenever an endpoint is saved;
the probe does not gate navigation. Runtime start remains gated only by local
configuration and sidecar readiness. The sidecar still does not call Mission
Control.

This probe proves that the configured origin serves the health contract. It
does not list Missions, WorkUnits, or other business state.

## Consequences

- Saved configuration and a live control plane are distinct user-visible
  facts.
- A malicious or mistaken URL cannot be used as an open redirect fetcher.
- Health-only success is no longer sufficient for `reachable`; see ADR-0101 for
  the authenticated session probe.
- Desktop now depends on a small HTTP client for this native command.

## Alternatives considered

- Have the Runtime sidecar probe Mission Control: rejected because the sidecar
  must not hold control-plane credentials or imply business readiness.
- Treat any HTTP response, including frontend HTML, as connected: rejected
  because that would claim Mission Control health for an unrelated origin.
- Require probe success before opening the browser: rejected because operators
  still need to open a temporarily down or login-gated console.

## Verification

- Native tests cover not-configured, loopback reachable health JSON, refused
  connections, 401, non-JSON 200, and ignored redirects.
- Snapshots and serialized probe output never contain the stored token or
  response body.
- The launcher refresh path renders reachability instead of configuration
  presence alone.
