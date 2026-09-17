# Verifier Service

> Status: minimum deployment candidate  
> Owner: verification maintainers  
> Last reviewed: 2026-08-16

This service hosts one workspace-scoped `VerifierWorker` behind a minimal
operational HTTP surface. It is independently authenticated from Runner and
reproduces registered Artifact byte integrity plus the Contract-bound
`artifact-set.v1` evaluation before requesting PASS Evidence. Mission Control
remains the only durable verification, Evidence, and lifecycle authority.

## Runtime contract

- Verifier identity, version, workspace, Mission Control origin, token file,
  and Artifact root are explicit startup configuration. There is no default
  tenant, workspace, verifier, or policy.
- The Mission Control bearer token is read from one bounded, non-symlinked,
  single-line file. A plaintext token setting is not supported.
- The token subject and `VERIFIER_ID` identify the same service principal.
  Grant that principal `mission:verify` in only the configured workspace.
- Redirects are disabled. Authentication is sent only to the configured
  Mission Control origin.
- This first deployment supports only `local:sha256/...` Artifact addresses
  under the explicitly mounted local CAS root. MinIO and other addresses fail
  before provider I/O; there are no implicit object-store credentials.
- The Artifact mount should be read-only for UID `10002`. The service writes no
  Artifact, Mission, WorkUnit, Evidence, queue, cursor, or verifier lease.
- Only the registered deterministic evaluator can produce PASS. Discovery
  context version 3 attributes an inconclusive policy to zero or more validated
  Contract criterion IDs, but the condition remains a typed failed poll. The
  service does not choose one criterion, submit INCONCLUSIVE Evidence, or own a
  durable Decision. Mission Control atomically opens that Decision and pauses
  the Mission; the next poll sees no eligible item until a human resolves it.

`/healthz` reports whether the worker task is alive. `/readyz` becomes ready
after a successful Mission Control discovery, including an idle result, and
returns only sanitized process-local counters. A failed poll makes readiness
false until Mission Control discovery and evaluation succeed again. Neither
endpoint exposes workspace, Mission, WorkUnit, Artifact, policy, error text, or
credential values.

## Required configuration

All variables use the `AGENTHUB_VERIFIER_` prefix:

| Variable suffix | Purpose |
|---|---|
| `VERIFIER_ID` | Stable service-principal identity used in Evidence admission |
| `VERIFIER_VERSION` | Explicit implementation version recorded in Evidence |
| `WORKSPACE_ID` | Authorized workspace passed to narrow verifier discovery |
| `MISSION_CONTROL_URL` | Exact absolute Mission Control HTTP(S) origin |
| `MISSION_CONTROL_TOKEN_FILE` | Absolute path to the mounted verifier token |
| `ARTIFACT_LOCAL_ROOT` | Absolute path to the shared read-only local CAS |

Optional bounded settings include `HOST`, `PORT`, `IDLE_DELAY_SECONDS`,
`MAX_DELAY_SECONDS`, `SHUTDOWN_TIMEOUT_SECONDS`, `HTTP_TIMEOUT_SECONDS`, and
`MAX_ARTIFACT_BYTES`.

## Run

From the repository root, with the required environment and mounts present:

```powershell
.\.venv\Scripts\python.exe -m services.python.verifier_service
```

Build from the repository root:

```powershell
docker build -f services/python/verifier_service/Dockerfile -t agenthub-verifier .
```

Run the image as its non-root user. Mount the token file and Artifact root
read-only and do not place token values in environment variables, image layers,
Mission data, or generated configuration.

## Deployment prerequisites

1. Mission Control exposes verifier discovery and Evidence admission and can
   authorize the mounted subject through workspace `mission:verify`.
2. Runner publishes registered local CAS addresses to storage mounted at the
   same content-addressed root in this process.
3. Mission Contracts bind eligible WorkUnit kinds to a supported deterministic
   evaluation policy.
4. Distinct replicas use distinct service principals when audit attribution is
   required. Duplicate evaluation is safe because Mission Control serializes
   Evidence admission.

## Shutdown and rollback

Graceful shutdown requests worker stop and waits for the active byte evaluation
or Evidence request. When the configured deadline expires, cancellation
propagates and the HTTP client is closed. There is no verifier lease to recover;
a request accepted concurrently by Mission Control remains durable, while an
unaccepted item remains discoverable.

To roll back, stop new verifier instances, allow the deadline to drain, and
replace the image. Do not edit WorkUnit status, delete Evidence, or create a
second queue. Repeated conflict or policy failures must be diagnosed from
Mission Control and low-cardinality service telemetry.
