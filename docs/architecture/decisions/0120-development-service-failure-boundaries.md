# ADR-0120: Seal privileged development services and fail closed on sandbox loss

> Status: accepted\
> Owner: platform, identity and execution maintainers\
> Date: 2026-10-17\
> Last reviewed: 2026-10-17\
> Scope: Go IAM/sandbox APIs and platform development Compose

## Context

IAM management could issue identities without an operator credential. Sandbox
transport could return simulated successful executions when Docker was absent,
and its in-container network allowlist setup could fail without preventing
execution. The full development Compose also enabled legacy knowledge and
execution services with unverified tenant/provider behavior by default.

## Decision

IAM is a privileged installation-operator API, not end-user login. Startup
requires a JWT signing key of at least 32 bytes and a distinct bounded token
file. All management/KMS paths require exactly one Bearer operator credential;
only health, profile and metrics are operational exceptions on the private
network. Token issuance and authorization reject missing/unassigned/inactive
principals and storage errors. Claims come from stored role/scope grants.
This seals the legacy surface; tenant-scoped identity remains a separate gate.

IAM runs only under the `iam-admin` Compose profile, with its token mounted as
a secret and no host-published port. All other development port publications
bind loopback. Legacy orchestration/runtime, model adapter, document, knowledge,
retrieval, summarization/evaluation and dedicated stores require the explicit
`legacy-experimental` profile. These profiles do not establish team readiness.

The sandbox retains a real Docker client when its daemon is unavailable; no
simulation is selected. `/healthz` is process liveness and `/readyz` checks the
daemon and supported configuration. Execution requires Docker and uses
`network=none`; nonempty allowlists are rejected until independently enforceable
egress policy exists. Docker's built-in seccomp default is used unless valid
configured JSON is supplied. A confirmed finished execution and actual exit
code are required; inspection/cleanup failures cannot imply success.

Cancellation and deadlines propagate to Engine calls and streams. Unconfirmed
execs force-remove their containers with a separate cleanup deadline. Failed
containers cannot be restarted/reused and remain available for delete retries.
The service still requires a private trusted network and administrative Docker
access; it is not an authenticated multi-tenant hostile-code platform.

## Compatibility and rollback

IAM callers must provision the new operator credential. Ordinary user JWTs
cannot access management routes. Development LAN access now needs an explicit
reviewed override. Previously accepted network allowlists now fail with 422;
unavailable Docker returns errors rather than `mode: noop`. Consumers must use
readiness for execution availability and handle failure explicitly.

Rollback disables new profile/backend admission and stops affected services,
without rewriting Mission state or deleting persisted data. Do not restore
unauthenticated IAM, unverified network fallback or simulated success. Rotate
the mounted IAM token and restart to revoke operator access.

## Verification and limits

Go HTTP tests cover credential rejection, stored claims, authorization failure,
missing Docker, daemon/inspection faults, deadlines, cancellation cleanup and
failed-container reuse. Deployment tests assert profile dependencies, mounted
identity and loopback publication; Compose configuration must validate with
each profile. Real Linux Docker isolation, tenant authorization, hard disk
quotas and durable orphan reconciliation need separate current-environment
evidence and are not established by mock daemon tests or container image builds.
