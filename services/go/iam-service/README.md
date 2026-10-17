# IAM administration service

This service is a privileged, installation-operator management API. It is not a
public login or end-user API. Mission/workspace authorization remains owned by
the control plane; future team login should use a separately verified identity
provider.

## Required configuration

Startup rejects missing/short `JWT_SECRET` (minimum 32 bytes) and a missing or
invalid `IAM_SERVICE_TOKEN_FILE`. The mounted file must contain a separate,
random, printable ASCII token of at least 32 characters (maximum file size
4,096 bytes). One trailing newline is permitted. Reusing the JWT signing key as
the operator token is rejected.

Every management request, including `/iam/token`, `/iam/verify`, tenant/role
administration, quotas, billing and KMS, requires exactly one
`Authorization: Bearer <operator service token>` header. Query parameters and
ordinary user JWTs do not authenticate this surface. `/healthz`, `/profile` and
`/metrics` are operational exceptions; keep them on the private service network.

The operator credential has installation-wide administration authority. Only
trusted administration clients receive it; it must never be sent to the browser,
model context, agents, logs or ordinary users. In particular this is not a
tenant-admin credential. Authentication proves the privileged operator; it does
not turn these legacy APIs into a tenant-scoped public service.

`POST /iam/token` is an authenticated operator identity-provisioning operation.
Requested tenant/user identities must have assigned roles in an active tenant
and an active user record. Database errors do not fall back to member permissions
or unsigned JWTs. Authorization evaluation also denies unavailable/unassigned
principals instead of evaluating an empty identity. Only stored scopes are issued; request-supplied roles/scopes
are rejected.

## Deployment and rollback

`deploy/docker-compose.platform.yml` starts IAM only with `--profile iam-admin`.
It exposes port 8088 to the Compose network, with no host publication, and mounts
the token from `AGENTHUB_IAM_SERVICE_TOKEN_FILE` (default
`deploy/secrets/iam-service-token`). JWT signing still uses `JWT_SECRET`.
Provision those values through the operator's secret mechanism before enabling
the profile. The development stack is not a team production topology.

Disable administration by stopping `iam-service` or removing its profile from
new startup commands. Rotate the mounted operator token and restart the service
to revoke administration clients. Do not roll back to unauthenticated routes;
preserve existing database state and separately manage JWT key rotation.

## Verification

Run `go test ./...` and `go vet ./...` in this directory. HTTP tests ensure
unauthenticated callers cannot reach token issuance or KMS/management handlers,
user JWT/query tokens cannot substitute for the operator credential, storage
failure cannot issue tokens, and issued claims match stored principal grants.
Deployment boundary tests are in
`tests/services/test_platform_deployment_boundaries.py`.
