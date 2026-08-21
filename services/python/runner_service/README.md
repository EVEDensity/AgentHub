# Runner Service

> Status: minimum deployment candidate  
> Owner: execution maintainers  
> Last reviewed: 2026-08-21

This service hosts the workspace-scoped `RunnerWorker` behind an operational
HTTP surface. It is deliberately not a queue, a fleet scheduler, or a verifier.
Mission Control remains the only durable WorkUnit state and discovery owner;
the process owns only one active claim attempt, request-scoped Harness
resources, Artifact byte publication, and sanitized health state.

## Runtime contract

- All Runner, workspace, Agent, and adapter identities are explicit startup
  configuration. There is no `default` tenant, Mission, Agent, or provider.
- Mission Control, AI Gateway, and Stateless MCP URLs are explicit absolute
  HTTP(S) URLs. Redirects are disabled.
- Mission Control, AI Gateway, and MCP bearer tokens are loaded from separate
  mounted files. The service never accepts plaintext token settings.
- The Mission Control token subject and `RUNNER_ID` identify the same service
  principal. Grant that principal the explicit `mission:claim` permission in
  the configured workspace ACL. Distinct replicas should use distinct
  principals; do not use administrator credentials. A legacy token subject
  equal to the workspace remains temporarily compatible but is not the target
  deployment model.
- The AI Gateway must implement non-streaming OpenAI-compatible
  `/chat/completions`, including `tools`, `tool_calls`, and provider usage. A
  `mock-*` model, malformed tool call, non-JSON body, remote error body, or
  response above the configured byte limit fails closed.
- The MCP binding manifest contains only capability names, function schemas,
  and descriptions. Endpoint and credentials are process configuration and
  cannot appear in the manifest. Every binding captures the exact Mission,
  WorkUnit, and attempt at Harness construction.
- Each model-backed Harness receives a request-scoped Mission Control checkpoint
  adapter bound to the claimed lease. The adapter forwards only phase, counters,
  usage, and bounded terminal failure metadata; it never forwards tool results,
  prompts, model responses, or credentials.
- The process declares exactly `a2a.inbound` and `mission.fork` in workspace
  claim requests. The set comes from the compiled resolver registry, not an
  environment setting. Each kind has its own context compiler and Harness
  policy; Mission Control filters unsupported WorkUnit kinds before locking or
  leasing. `a2a.delegate` and `a2a.outbound` remain outside this runtime.
- Artifact output uses a content-addressed local root. A deployment must mount
  that root on storage readable by the Mission Control verifier.

`/healthz` reports whether the worker task is alive. `/readyz` becomes ready
after a successful Mission Control claim request, including `idle` and
`capacity_saturated` outcomes. Its worker snapshot separates those counters and
exposes only the last low-cardinality claim status. Readiness does not probe the
AI Gateway or every MCP tool, because doing so would execute provider-specific
operations outside a WorkUnit. Both endpoints exclude tenant, quota, objective,
prompt, tool, and credential content.

The application layer retains a controlled Mission-fork composition for one
explicitly selected Mission as a narrow integration surface. The service
runtime uses the separate kind-aware workspace composition, whose mixed-kind
ASGI gate proves both eligible model-backed roots resolve through their exact
compiler and lease-fenced execution path.

The runtime smoke test also starts a real `RunnerWorker` over the same
composition and drives one `mission.fork` root through workspace claim,
lease-fenced context, controlled start, five checkpoints, content-addressed
Artifact publication, registration, and completion to `VERIFYING`. It uses
in-process Mission Control and model ports only; it is not a substitute for a
deployment-specific network, mounted-secret, or external AI Gateway check.

## Required configuration

All variables use the `AGENTHUB_RUNNER_` prefix:

| Variable suffix | Purpose |
|---|---|
| `RUNNER_ID` | Stable process identity used for lease ownership |
| `WORKSPACE_ID` | Authorized workspace passed to atomic ready-work discovery |
| `ASSIGNED_AGENT_ID` | Catalog-bound Agent identity accepted by claims |
| `ASSIGNED_ADAPTER` | Catalog-bound adapter; `a2a.outbound` is forbidden |
| `MISSION_CONTROL_URL` | Mission Control origin |
| `MISSION_CONTROL_TOKEN_FILE` | Read-only bearer token file |
| `MODEL_GATEWAY_URL` | OpenAI-compatible base URL, normally ending in `/v1` |
| `MODEL_GATEWAY_TOKEN_FILE` | Read-only AI Gateway bearer token file |
| `MODEL` | Explicit non-mock model identifier |
| `MCP_ENDPOINT` | Exact Stateless MCP JSON-RPC endpoint |
| `MCP_TOKEN_FILE` | Read-only platform IAM bearer token file |
| `MCP_BINDINGS_FILE` | Versioned credential-free binding manifest |
| `ARTIFACT_LOCAL_ROOT` | Shared content-addressed Artifact volume |

Polling, heartbeat, shutdown, context, model response, Harness, token, and
Artifact limits have bounded defaults in `config.py` and can be overridden with
their corresponding prefixed field names. See `mcp-bindings.example.json` for
the manifest schema; do not deploy that example unless its capability and tool
are actually registered by the MCP Gateway.

## Outbound cutover preparation

`a2a_peers.py` defines a versioned `agenthub.runner.a2a-peers.v1` startup
manifest for the future outbound composition. Each peer entry contains one
exact HTTP(S) origin, one or more Ed25519 public-key pins for rotation, and an
optional absolute path to a receiver-issued Bearer token file. The manifest
cannot contain a token value, provider configuration, unsigned compatibility,
or two peers that share a token file. Token files are bounded, single-line,
visible ASCII secrets and symbolic links are rejected.

Loading this manifest produces a strict signed-and-pinned trust policy plus an
exact-origin credential provider. It does not alter `RunnerServiceSettings`,
claim `a2a.outbound`, or compose the transport into the worker. The application
layer now has isolated attempt and single-poll outbound coordinators. A low-level
`compose_a2a_outbound_runtime_candidate` can combine those coordinators with an
explicit Mission Control port, CAS publisher, loaded peer policy, peer HTTP
client, and transferred closeable resources. The candidate drains or cancels
active work before closing peer/control resources, but it is not called by
`create_app`, does not read environment settings or secrets, and cannot become
the default runtime accidentally. Runtime settings still reject
`a2a.outbound`. Enabling that adapter and removing Gateway request-path dispatch
remain one atomic cutover; operators must not deploy this file as evidence that
outbound Runner execution is enabled.

## Run

From the repository root, with the required environment and mounts present:

```powershell
.\.venv\Scripts\python.exe -m services.python.runner_service
```

Build the container from the repository root so the Dockerfile can copy the
control-plane modules:

```powershell
docker build -f services/python/runner_service/Dockerfile -t agenthub-runner .
```

Run the container as its non-root user, mount each token file read-only, and
mount the Artifact root read-write. Mounted token files must be readable by UID
`10001`, and the Artifact directory must be writable by that UID. Do not place
token values in image layers, environment variables, the MCP manifest, or
Mission/WorkUnit configuration.

## Deployment prerequisites

1. Mission Control exposes the atomic bound-claim and lease-fenced execution
   context APIs and can authorize the mounted Runner principal through the
   workspace ACL `mission:claim` permission.
2. The configured workspace catalog contains the exact Agent/adapter binding
   used by eligible inbound and Mission-fork Missions.
3. The workspace belongs to an active IAM tenant with a valid plan quota or
   numeric `max_concurrent` override. `0` explicitly means unlimited; missing
   or malformed admission policy prevents new claims.
4. The AI Gateway supports the configured model and OpenAI tool schemas without
   a mock fallback.
5. The Stateless MCP endpoint authenticates the mounted IAM token and enforces
   the forwarded capability scope on every call.
6. The Artifact volume is durable and shared with the independent verifier.

## Rollback

Stop new Runner instances first and allow the shutdown deadline to drain the
active claim. If the deadline expires, cancellation is propagated and the
leased attempt is reported failed when Mission Control is reachable. Replace
the image with the previous version without modifying Mission data. Any lease
left by a process or network failure must expire and enter Mission Control's
existing retry/failure recovery path; operators must not edit WorkUnit state or
requeue it in a second store.

The worker consumes Mission Control's workspace-scoped atomic ready-work
contract. Deploy each process with one explicit workspace and Agent/adapter
binding. Scaling replicas increases concurrent claim capacity through Mission
Control row locking; it does not create a Runner-owned queue or provide fleet
priority or Agent-specific capacity routing. Tenant concurrency admission stays
in Mission Control.

Mission Control checks `mission:claim` on every new claim, so removing the
permission prevents further leases immediately. An already claimed attempt uses
its lease owner and lease ID as the execution fence and may still report
heartbeat, Artifact metadata, completion, or failure after grant revocation.

The same claim reads the tenant's effective Runner concurrency limit. Capacity
is measured from live Mission WorkUnits rather than a Runner-local counter. At
the limit, the claim returns `capacity_saturated` with no WorkUnit and the
worker uses normal bounded idle backoff while recording only a process-local
operational count.
