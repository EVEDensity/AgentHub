# ADR-0065: Local Decision Expiry Deployment Profile

> Status: accepted  
> Owner: Mission Control and operations maintainers  
> Date: 2026-08-16  
> Scope: local Compose activation and container constraints

## Context

ADR-0064 created a separately runnable Decision expiry supervisor but left it
out of deployment manifests until composition could be verified. Operators need
a repeatable local path to validate startup, readiness, PostgreSQL interaction,
and shutdown without adding the maintenance process to every Community stack or
presenting local credentials as a production secret design.

## Decision

The platform Compose file provides a `mission-supervision` profile containing
the Decision expiry service. Profiles are opt-in, so the default platform
service set remains unchanged. The service waits for the bundled PostgreSQL
health check and its own container health check calls `/readyz`, not `/healthz`.
Database connectivity without a successful expiry command is therefore
insufficient for readiness.

The local container publishes no host port and mounts no durable volume. It
runs with an init process, a read-only root filesystem, all Linux capabilities
dropped, `no-new-privileges`, and bounded temporary filesystems. Compose's stop
grace period exceeds the supervisor's internal drain deadline so the process can
cancel a stuck command and close its database pool before container termination.

The profile uses the bundled local PostgreSQL credential by default and permits
an explicit `AGENTHUB_DECISION_EXPIRY_DATABASE_URL` override. This is a local
development mechanism only. It is not an approved production credential path,
and production enablement remains blocked until database credentials can be
mounted through a dedicated secret composition and an operational review is
complete.

## Consequences

Developers can validate the real container and readiness contract without
silently enabling automatic expiry in ordinary deployments. A missing migration
or failed command leaves the container not ready. Stopping the profile pauses
supervision while persisted deadlines and committed transitions remain intact.

The platform Compose still contains local cleartext infrastructure credentials;
this ADR does not promote them to production. No claim of production automatic
expiry may be made from the existence of this profile alone.

## Alternatives considered

- Add the service to the default Compose set: rejected because supervision must
  be an explicit operational choice until production gates are complete.
- Publish port 8099 to the host: rejected because probes can run inside the
  Compose network and there is no user-facing API.
- Use `/healthz` for container readiness: rejected because a live worker with
  failing database commands is not ready to enforce expiry.
- Add a local queue or volume: rejected because PostgreSQL is the durable source
  of eligibility and transitions.
- Call the operator's current `.env` database during tests: rejected because
  deployment contract validation must not mutate unknown real state.

## Verification

A structured Compose contract test verifies opt-in activation, the dedicated
image, PostgreSQL health dependency, absence of host ports and durable volumes,
container restrictions, readiness probing, and shutdown timing. `docker compose
config` validates the fully interpolated profile without starting containers.

## Extends

This decision deploys the local validation follow-up from
[ADR-0064](0064-independent-decision-expiry-supervisor.md) without changing its
Mission Control ownership model.
