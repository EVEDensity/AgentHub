# Runner Service

> Status: minimum deployment candidate  
> Owner: execution maintainers  
> Last reviewed: 2026-08-15

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
- Artifact output uses a content-addressed local root. A deployment must mount
  that root on storage readable by the Mission Control verifier.

`/healthz` reports whether the worker task is alive. `/readyz` becomes ready
after a successful Mission Control claim request, including an empty claim.
Readiness does not probe the AI Gateway or every MCP tool, because doing so
would execute provider-specific operations outside a WorkUnit. Both endpoints
expose only counters, timestamps, delay, and exception type.

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
   used by eligible inbound Missions.
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
the limit, the claim returns empty and the worker uses normal bounded idle
backoff.
