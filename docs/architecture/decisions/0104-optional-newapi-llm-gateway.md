# ADR-0104: Optional new-api LLM Gateway (supplier layer)

> Status: accepted
> Owner: backend maintainers
> Date: 2026-08-26
> Scope: LLM supplier adapter layer (`app/services/adapter_manager.py`,
> `services/python/model_adapter_service`), deployment assets, model
> configuration migration

## Context

AgentHub talks to LLM providers through a self-hosted per-provider adapter
layer (`OpenAICompatibleAdapter` subclasses for OpenAI/Anthropic/Qwen/
DeepSeek/Doubao/Kimi/Zhipu/Minimax/Ollama/vLLM). This layer works well for
single-user and private deployments, where one key per provider and direct
routing are acceptable.

Some deployments need more: multiple keys per provider with failover,
per-tenant quotas and billing, usage analytics, or a single OpenAI-compatible
entry exposed to external callers. Building these into the self-hosted adapter
layer is high-cost maintenance. new-api (and one-api/LiteLLM-class projects)
provides the aggregation, quota, billing and admin surface out of the box.

The Go control-plane gateway (`gateway-service`, `:8081`) is a different
layer: it owns API routing, auth, audit, A2A and MCP adapters. It is **not**
an LLM aggregation gateway and is out of scope for this decision.

## Decision

The new-api LLM gateway is adopted as an **optional, opt-in** supplier layer.

- Default remains the self-hosted per-provider adapters (`AGENTHUB_LLM_GATEWAY`
  empty). No deployment regresses and no new runtime dependency is added.
- When `AGENTHUB_LLM_GATEWAY=newapi`, the application's remote provider/model
  calls fan out through one OpenAI-compatible entry configured by
  `AGENTHUB_NEWAPI_BASE_URL` / `AGENTHUB_NEWAPI_API_KEY`.
- In gateway mode, local adapters (`mock`, `local_claude`, `local_codex`,
  `local_openclaw`, `cloud_code`) keep their own paths; embedding/rerank
  endpoints in `model_adapter_service` (`bge`) stay local.
- Provider configuration migrates into new-api channels via
  `deploy/newapi/migrate_models.py` (reads `model_configs` + env keys,
  creates channels + one aggregated token, writes a redacted report,
  supports `--dry-run` and is idempotent).
- new-api is packaged as an optional compose stack
  (`deploy/docker-compose.newapi.yml`) with a local OpenAI-compatible
  `mock-llm` canary upstream for offline end-to-end verification.

### Explicitly out of scope

- Replacing the Go control-plane gateway (`gateway-service`).
- Removing the self-hosted adapter layer; it remains the default path and the
  instant fallback for rollback.

## Consequences

- Positive: quota/billing/failover/analytics for multi-tenant or key-pooled
  deployments without growing the adapter layer; one entry for external
  consumers; canary-channel e2e tests offline via `mock-llm`.
- Cost: an extra service to operate when enabled; channel/model mapping must
  be kept in sync (report + new-api admin console); token billing parity with
  native providers is measured by the `cn_tokenizer_precision` bench gate and
  is a target until native tokenizers are provisioned.
- Security: gateway keys are plaintext in config/env; the deployment doc must
  require HTTPS or loopback binding for the gateway entry.