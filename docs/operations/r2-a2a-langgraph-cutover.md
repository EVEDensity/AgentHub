# R2 Runbook: A2A Outbound Cutover, LangGraph Decommission, Test Normalization

> Status: implemented
> Owner: architecture maintainers
> Last reviewed: 2026-08-26
> Scope: ADR-0053 cutover switch, legacy LangGraph flag, test placement
> Applies to: deployers of the Mission Runner profile and the Gateway

## 1. What changed

### 1.1 A2A outbound atomic cutover (ADR-0053)

- **Gateway** (`services/go/gateway-service/cmd/gateway-service/a2a_handler.go`):
  remote dispatch in `tasks/send` and `tasks/cancel` is now controlled by
  `A2A_DISPATCH_MODE`:
  - `gateway` (default): legacy request-path forwarding, for compatibility.
  - `runner`: the Gateway only submits to Mission Control; the Runner-supervised
    outbound A2A worker owns remote dispatch. The two paths never dispatch the
    same attempt.
- **Runner service** (`services/python/runner_service/`): the production
  `build_runner_runtime` now composes the outbound runtime candidate when
  `assigned_adapter=a2a.outbound`. It requires a strict peer manifest
  (`a2a_peers_file`) and `source_agent_url`; missing values fail closed.
- **Compose** (`deploy/docker-compose.platform.yml`): `runner-service` now
  accepts `AGENTHUB_RUNNER_A2A_PEERS_FILE` and `AGENTHUB_RUNNER_SOURCE_AGENT_URL`.

### 1.2 Legacy LangGraph decommission

- `app/services/message_router.py` routes directly to `call_agent` by default.
  The legacy LangGraph orchestration runs only when
  `AGENTHUB_ENABLE_LEGACY_LANGGRAPH=true` (migration window only).
- Production chat no longer writes through the legacy DAG engines by default;
  the enterprise Mission/WorkUnit path remains the execution-of-record.

### 1.3 Test normalization

- `app/db/test_sqlite_pool.py` → `tests/persistence/`
- `app/api/test_websocket_{state,dispatch,message_flow}.py` → `tests/api/`

## 2. Monitoring

Gate/switch truth to watch after deployment:

| Signal | Healthy | Broken |
|---|---|---|
| `a2a_dispatch_mode` (Gateway log line) | `mode=runner` after cutover | `mode=gateway` when runner was intended |
| `a2a_task_requests_total{tasksSend}` counters | submissions continue | submissions drop to zero unexpectedly |
| Runner `/readyz` `worker.status` | `ready` with `claimStatus=idle` or `claimed` | `unready` with repeated failure backoff |
| Mission WorkUnit status (control plane) | `a2a.delegate` roots reach `VERIFYING` | roots stuck `PENDING`/`LEASED` |
| LangGraph legacy path | `AGENTHUB_ENABLE_LEGACY_LANGGRAPH` unset/`false` | chat routes through legacy DAG tables |

Prometheus/Grafana: reuse existing `a2a_*` metrics from `a2a_handler.go` and
Runner `snapshot` counters.

## 3. Rollback trigger conditions

Roll back (restore `A2A_DISPATCH_MODE=gateway` and/or
`AGENTHUB_ENABLE_LEGACY_LANGGRAPH=true`) when any of these is observed:

1. Outbound A2A roots remain `PENDING`/`LEASED` beyond the Contract budget with
   no Runner lease owner (Runner not claiming).
2. Remote peers report duplicate dispatch for one task id (both Gateway and
   Runner attempted the same attempt).
3. Runner `/readyz` reports `unready` continuously for > 5 minutes after a
   valid claim.
4. Chat messages fail or lose history after the LangGraph flag flip
   (frontend e2e or user report).
5. Artifact byte verification errors recur on outbound result import.

Rollback is a configuration flip, not a code revert:

- Gateway: `A2A_DISPATCH_MODE=gateway` (default), keep Runner profile.
- LangGraph: `AGENTHUB_ENABLE_LEGACY_LANGGRAPH=true`.
- Re-deploy the affected process and re-run the verification steps below.

## 4. Verification

```bash
# 1. Gateway unit + build
go build ./gateway-service/cmd/gateway-service
go test -short -count=1 ./gateway-service/cmd/gateway-service/ -run "A2A"

# 2. Runner config contracts
pytest tests/services/test_runner_service_primitives.py \
       tests/services/test_runner_service_entrypoint.py \
       tests/services/test_a2a_outbound_runtime.py -q

# 3. LangGraph flag
pytest tests/services/test_message_router.py -q

# 4. Normalized tests
pytest tests/persistence/test_sqlite_pool.py \
       tests/api/test_websocket_state.py \
       tests/api/test_websocket_dispatch.py \
       tests/api/test_websocket_message_flow.py -q

# 5. Full suite
pytest tests/ -q
```

## 5. Stop conditions (from the reconstruction roadmap)

- No new business state written through the legacy LangGraph/DAG path by
  default; the flag stays off in production.
- Outbound A2A dispatch is owned by exactly one path: either the Gateway
  (compatibility) or the Runner (ADR-0053), never both.
- All structural tests live under `tests/`.