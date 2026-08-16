# Decision Expiry Service

> Status: minimum deployment candidate  
> Owner: Mission Control maintainers  
> Last reviewed: 2026-08-16

This service runs the fail-closed Decision expiry command outside the Web
process. It drains already-expired PENDING Decisions one transaction at a time;
Mission Control remains the only owner of the Decision, WorkUnit, Mission, and
event transitions.

## Runtime contract

- The process reads its PostgreSQL DSN from one required, absolute
  `DATABASE_URL_FILE` and performs a real connectivity check before starting
  its worker. A plaintext DSN setting is not supported.
- Expiry uses a process-owned PostgreSQL wire pool and one real connection-level
  transaction. Stateless Neon HTTP transaction emulation is not supported;
  Neon deployments must supply a direct PostgreSQL DSN.
- A successful expiry is polled again immediately. An idle result or transient
  failure uses bounded exponential backoff.
- The worker owns no queue, cursor, schedule, workspace projection, or durable
  state. Multiple replicas rely on the command's transaction locks and
  `SKIP LOCKED` selection.
- Decision TTL is not process configuration. `expiresAt` was fixed when the
  Decision was created and is never recomputed by this service.
- Shutdown interrupts an idle wait, lets an active transaction finish within
  the configured deadline, then cancels the task and closes the database pool.
- `/healthz` reports whether the worker task is alive. `/readyz` becomes ready
  only after a real successful expiry command, including an idle result.
- Probes expose only low-cardinality counters, timestamps, status, and exception
  type. They never expose database URLs, Mission IDs, Decision IDs, WorkUnit
  IDs, policy content, or error messages.

## Configuration

All settings use the `AGENTHUB_DECISION_EXPIRY_` prefix:

| Variable suffix | Default | Purpose |
|---|---:|---|
| `DATABASE_URL_FILE` | required | Absolute path to a mounted, single-line PostgreSQL DSN |
| `HOST` | `0.0.0.0` | Operational HTTP bind host |
| `PORT` | `8099` | Operational HTTP port |
| `IDLE_DELAY_SECONDS` | `0.5` | Initial idle/error backoff |
| `MAX_DELAY_SECONDS` | `10.0` | Maximum idle/error backoff |
| `SHUTDOWN_TIMEOUT_SECONDS` | `30.0` | Active-command drain deadline |

Do not add a Decision timeout setting here. Governance SLA changes belong to
immutable Contract revision and Decision creation, not expiry supervision.

## Run

From the repository root with an absolute secret-file path configured:

```powershell
$env:AGENTHUB_DECISION_EXPIRY_DATABASE_URL_FILE = "D:\secrets\agenthub-database-url"
.\.venv\Scripts\python.exe -m services.python.decision_expiry_service
```

Build from the repository root:

```powershell
docker build -f services/python/decision_expiry_service/Dockerfile -t agenthub-decision-expiry .
```

The image runs as non-root UID `10003`. Mount the database credential read-only
and pass only its in-container path. Do not bake the DSN into the image,
environment, command line, or a tracked file.

## Deployment status and rollback

The checked-in platform Compose offers a default-disabled
`mission-supervision` profile for local validation. It does not publish the
operational port to the host and is not an approved production secret path.
Before enabling it, run migrations through Alembic head, verify PostgreSQL
connectivity, exercise health and readiness probes, and confirm expiry counters
against Mission events. The runtime now enforces mounted-secret database
composition; production deployment remains blocked on operational review and a
successful isolated direct-PostgreSQL smoke gate. The gate is available at
`scripts/decision_expiry_smoke.py` and never targets the repository `.env`
database.

Rollback by stopping the service. In-flight committed transitions remain
durable; pending expired Decisions remain eligible for a later replica. Never
repair rollback by editing Decision, WorkUnit, or Mission rows manually.
