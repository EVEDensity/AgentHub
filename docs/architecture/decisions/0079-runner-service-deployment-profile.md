# ADR-0079: Deploy the Model Runner Through an Opt-In Profile

> Status: accepted  
> Date: 2026-08-21  
> Owners: execution and operations maintainers

## Context

The kind-aware model Runner is reachable from the deployable Python service,
but deployment must not silently introduce external endpoint defaults,
credentials, writable state, or outbound A2A transport. The generic platform
Compose topology does not own a Mission Control HTTP service, a production AI
Gateway, or a Stateless MCP endpoint.

## Decision

The platform Compose file defines a default-disabled `mission-runner` profile.
The profile mounts separate Mission Control, AI Gateway, and MCP token files;
mounts the credential-free MCP binding manifest read-only; and permits writes
only to the configured shared Artifact root. It publishes no host port and has
no `depends_on` relationship to imply ownership of external endpoints.

Compose permits the base topology to parse without Runner settings. When the
profile starts, the Runner's existing strict settings validation rejects empty
identity or endpoint values and rejects `a2a.outbound`. The profile activates
only the kind-aware model/Harness path for `a2a.inbound` and `mission.fork`.

## Consequences

- Operators explicitly supply deployment identity, endpoints, mounted secrets,
  manifest, and Artifact storage before enabling the Runner.
- Normal platform startup remains unchanged.
- A missing Artifact root or binding manifest causes mount/start failure rather
  than an implicit host-path creation.
- Remote A2A transport deployment remains outside this profile and requires
  its separate atomic Gateway cutover.

## Verification

Deployment contract tests require the profile, security settings, file-backed
credentials, read-only manifest mount, Artifact mount, internal readiness
probe, no host port, no local service dependency, and shutdown deadline.
`docker compose config --quiet` validates the base Compose document.
