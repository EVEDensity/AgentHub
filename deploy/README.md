# Deployment Assets

`deploy/` contains local and production deployment support: Compose files,
images, observability configuration, and operational defaults.

Every deployment document must state prerequisites, exposed ports, secrets,
health checks, persistence, rollback, and the verification command. Local
Community deployment should not require the full enterprise service topology.

Do not use deployment configuration to silently change domain semantics. A
feature unavailable in a deployment must fail explicitly and be observable.

## Local Decision expiry supervision

The Decision expiry supervisor is available only through the explicit
`mission-supervision` profile. Normal platform startup does not run it. Before
enabling the profile, migrate the local PostgreSQL database to the current
Alembic head; otherwise the process remains not ready and must not be treated as
providing automatic expiry.

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
New-Item -ItemType Directory -Force deploy/secrets
Set-Content -NoNewline deploy/secrets/decision-expiry-database-url "postgresql://agenthub:agenthub@postgres:5432/agenthub"
docker compose -f deploy/docker-compose.platform.yml --profile mission-supervision up -d --build postgres decision-expiry-service
docker compose -f deploy/docker-compose.platform.yml --profile mission-supervision ps decision-expiry-service
```

The profile waits for PostgreSQL health and probes the supervisor's `/readyz`
endpoint inside the container. Port `8099` is exposed only to the Compose
network, not published to the host. The container runs with no Linux
capabilities, a read-only root filesystem, bounded temporary filesystems, and no
durable volume. Mission Control PostgreSQL remains the only persistence layer.

Compose mounts `deploy/secrets/decision-expiry-database-url` by default. Set
`AGENTHUB_DECISION_EXPIRY_DATABASE_URL_FILE` to select a different host-side
file. The directory is excluded from Git and the Docker build context. The DSN
must use PostgreSQL wire protocol; the supervisor deliberately does not use the
stateless Neon HTTP adapter because expiry requires one real transaction.

The example credentials above are for the bundled local PostgreSQL container
only. Production must source the mounted file from its secret manager and pass
only the file path in process configuration. A real direct-PostgreSQL smoke test
and operational review are still required before production enablement.

Stop or roll back supervision without editing durable Mission state:

```powershell
docker compose -f deploy/docker-compose.platform.yml --profile mission-supervision stop decision-expiry-service
```

A committed expiry transaction remains authoritative. Any still-pending expired
Decision remains eligible when a compatible supervisor starts again.

### Isolated expiry smoke gate

The smoke gate uses a dedicated Compose topology, random loopback ports, a
generated password, and a DSN file under the operating-system temporary
directory. It does not load the repository `.env` or use the platform Compose
database. The script migrates the temporary PostgreSQL database, inserts one
valid expired Decision, builds and starts the supervisor, and verifies:

- Decision `PENDING -> EXPIRED` with service resolution metadata;
- WorkUnit `VERIFYING -> FAILED` and Mission `WAITING_DECISION -> FAILED`;
- exactly three causally linked aggregate events;
- no Evidence and no duplicate events after subsequent idle polls;
- sanitized readiness counters with exactly one expiry.

Docker and the repository Python dependencies are prerequisites. Run from the
repository root:

```powershell
.\.venv\Scripts\python.exe scripts/decision_expiry_smoke.py
```

The script assigns a unique Compose project and executes `down --volumes` in a
`finally` block. A cleanup failure fails an otherwise successful run. If the
test itself fails and cleanup also fails, the original failure is preserved and
the cleanup error type is reported so the operator can remove that exact smoke
project without touching other containers.

## A2A trust policy

Gateway rejects unsigned A2A Agent Cards by default. Development environments
that intentionally interoperate with unsigned agents must set
`A2A_ALLOW_UNSIGNED_CARDS=true`; do not use that override in production.

Production deployments can pin one or more Ed25519 public keys to each agent
origin. Multiple keys allow an old and new key to overlap during rotation:

```text
A2A_REQUIRE_PINNED_KEYS=true
A2A_TRUSTED_PUBLIC_KEYS_JSON={"https://agent.example.com":["<hex-ed25519-public-key>","<next-hex-ed25519-public-key>"]}
```

The JSON keys must be HTTP(S) origins without paths or queries. Invalid
booleans, origins, JSON, empty key lists, or non-Ed25519 keys prevent Gateway
startup. `A2A_ALLOW_UNSIGNED_CARDS=true` and
`A2A_REQUIRE_PINNED_KEYS=true` are mutually exclusive. `GET
/platform/a2a/trust-status` exposes only policy flags and the number of pinned
origins; it never exposes key material.

To publish a signed AgentHub Card, mount a persistent installation identity key
as a read-only Secret and pass only its path to Gateway:

```text
A2A_REQUIRE_SIGNED_SELF_CARD=true
A2A_CARD_SIGNING_KEY_FILE=/run/secrets/agenthub_a2a_ed25519
```

The file must contain a hex-encoded 32-byte Ed25519 seed or 64-byte Ed25519
private key. A seed can be generated outside the repository with
`openssl rand -hex 32`; store it in the deployment secret manager, not in an
environment variable or tracked file. Rotate by pinning the new public key on
peers before replacing the mounted key and restarting Gateway.

For a non-exportable KMS/HSM key, configure a controlled remote signer instead
of mounting private key material in Gateway:

```text
A2A_REQUIRE_SIGNED_SELF_CARD=true
A2A_CARD_SIGNER_URL=https://signer.internal/v1/a2a-card
A2A_CARD_SIGNER_KEY_ID=agenthub-production-card
A2A_CARD_SIGNER_TOKEN_FILE=/run/secrets/agenthub_a2a_signer_token
```

`A2A_CARD_SIGNER_URL` and `A2A_CARD_SIGNING_KEY_FILE` are mutually exclusive.
The token file is bounded, must contain one non-empty line, and must be mounted
read-only. The signer URL must use HTTPS, must not contain credentials, query,
or fragment, and never follows redirects. For local tests only, loopback HTTP
can be enabled with `A2A_CARD_SIGNER_ALLOW_INSECURE_HTTP=true`.

The endpoint accepts `POST application/json` with one of these request shapes:

```json
{"operation":"public_key","purpose":"a2a_agent_card_v1","key_id":"agenthub-production-card"}
{"operation":"sign","purpose":"a2a_agent_card_v1","key_id":"agenthub-production-card","key_version":"42","payload":"<base64-card-json>"}
```

Responses use `algorithm`, `key_id`, `key_version`, and either a hex
`public_key` or hex `signature`. The signer must authorize the fixed purpose,
caller, and key ID; it must never return private key material. Gateway pins the
reported version for the startup signing operation, verifies the returned
signature locally, and publishes only non-secret identity metadata. Rotation
still follows peer-pin overlap: publish the new public pin, switch signer key
version and restart Gateway, then remove the old pin.

AgentHub-to-AgentHub delegation also requires a receiver-issued bearer token
for each peer origin. Mount each token as a separate read-only Secret and map
the peer origin to the in-container file path; the environment variable carries
paths only, never token values:

```text
A2A_PEER_BEARER_TOKEN_FILES_JSON={"https://peer.example.com":"/run/secrets/peer_example_a2a_token"}
```

Origins use the same exact HTTP(S) origin rules as public-key pins. Token files
are bounded to 16 KiB and must contain one non-empty line. A peer whose Agent
Card advertises Bearer authentication cannot receive a task unless its origin
has a configured token. Gateway never substitutes the caller's Authorization
header. Peer tokens are loaded at startup, are used only for the matching
origin, and never enter Agent Cards, Registry data, trust status, Mission,
WorkUnit, Artifact, Evidence, or logs. Rotation requires replacing the mounted
Secret and restarting Gateway.

The public Agent Card is served at `/.well-known/agent-card.json`. Local clients
submit outbound work to the authenticated `/platform/a2a/tasks` endpoint;
peers call the authenticated `/platform/a2a/inbox` endpoint declared by the
Card. Do not expose the inbox without Gateway IAM verification.
