# ADR-0066: File-Backed Transactional Supervisor Database

> Status: accepted  
> Owner: Mission Control, security, and operations maintainers  
> Date: 2026-08-16  
> Scope: Decision expiry database credentials and transaction transport

## Context

ADR-0064 initially composed the Decision expiry service through the process
global `DATABASE_URL` and shared database singleton. ADR-0065 correctly marked
that environment-variable path as local-only and blocked production on mounted
secrets. Removing plaintext configuration also exposes a deeper correctness
requirement: expiry locks and updates Decision, WorkUnit, Mission, and events in
one transaction.

The existing Neon HTTP adapter sends each SQL statement as an independent HTTP
request. Separate `BEGIN`, work, and `COMMIT` requests cannot establish one
PostgreSQL session transaction, so that adapter cannot safely execute the expiry
command even though it presents a transaction-shaped interface.

## Decision

The Decision expiry service requires an absolute
`AGENTHUB_DECISION_EXPIRY_DATABASE_URL_FILE`. The file must be a bounded,
non-empty, UTF-8, single-line regular file and must not be a symbolic link. Its
value must be a `postgres://` or `postgresql://` DSN with a host and database
name. Plaintext database URL configuration is unsupported.

The service owns a dedicated `AsyncPgPool` and injects its operations and
transaction factory into `MissionRepository`. Each expiry command acquires one
pool connection and holds its native transaction across selection locks,
revalidation, state updates, and event writes. The process does not import or
mutate the control plane's global database session.

Neon remains supported only through its direct PostgreSQL wire endpoint for
this service. If direct transport is unavailable, the supervisor fails closed;
it must not downgrade to stateless HTTP or split the atomic command.

Database cleanup is armed before initialization begins. A partially initialized
pool is therefore closed when connectivity validation fails, as well as after
worker startup failure, graceful shutdown, or forced cancellation.

The local Compose profile mounts the DSN as a read-only Compose secret and puts
only the in-container file path in the environment. The host secret directory
is excluded from Git and the Docker build context.

## Consequences

The DSN no longer appears in normal container environment inspection, process
arguments, tracked configuration, or build context. The supervisor transaction
now has the connection affinity required by `FOR UPDATE SKIP LOCKED` and atomic
event/state persistence.

Direct PostgreSQL connectivity becomes a deployment prerequisite. Environments
that only permit Neon HTTP cannot run automatic Decision expiry until they
provide a transaction-capable transport. This availability tradeoff is required
for correctness and fail-closed behavior.

## Alternatives considered

- Read a file and copy its value into `os.environ`: rejected because it retains
  the global singleton and makes the secret ambient process configuration.
- Keep the environment DSN for local mode: rejected because two credential
  paths create drift and make production mistakes easier.
- Continue using Neon HTTP transaction emulation: rejected because independent
  HTTP requests do not preserve session transaction state.
- Add a database proxy or queue: deferred because direct PostgreSQL already
  supplies the required transaction and no new durable truth is needed.

## Verification

Configuration tests cover required absolute paths, plaintext-setting rejection,
bounded single-line files, URL schemes, hosts, database names, fragments, and
ports. Runtime tests verify one acquired connection and native transaction
scope, strict MissionService composition, cleanup after partial initialization,
and sanitized probes. Deployment tests verify the read-only secret mount and
absence of plaintext `DATABASE_URL` environment configuration.

## Supersedes

This decision supersedes the `DATABASE_URL` composition in
[ADR-0064](0064-independent-decision-expiry-supervisor.md) and the temporary
local environment override in
[ADR-0065](0065-local-decision-expiry-deployment-profile.md). Their process
ownership, polling, and container constraints remain accepted.
