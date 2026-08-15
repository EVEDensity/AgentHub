# ADR-0046: Strict Runner Process Boundary

> Status: accepted  
> Owner: Runner, Harness, and platform security maintainers  
> Date: 2026-08-15  
> Scope: process composition, credentials, AI/MCP adapters, and shutdown

## Context

ADR-0045 introduced a process-local worker but intentionally stopped before a
deployable entry point. Reusing the existing model adapter would violate the
execution boundary: it can route unsupported models to `MockProvider`, does not
carry the request-scoped tool schemas required by Harness, and may expose
provider error content. Loading provider or control-plane credentials directly
from environment variables would also spread plaintext secrets across process
configuration and deployment metadata.

The initial worker still requires an explicit Mission ID because Mission
Control has no workspace-authorized global ready-work discovery contract.
Runner must not close that gap with a local queue or legacy task scan.

## Decision

Add an independently runnable Python Runner service that composes the accepted
single-Mission worker. Runner, Mission, Agent, adapter, and every network
location are required startup values. Bearer credentials for Mission Control,
the AI Gateway, and MCP are separate mounted files; no plaintext credential
setting or combined credential manifest is accepted.

The service uses a strict non-streaming OpenAI-compatible AI Gateway adapter.
It forwards the exact request-scoped function schemas, rejects mock models and
redirects, bounds decoded response bytes, validates message/tool-call/usage
shape, and never includes upstream response content in raised errors. Each
Harness gets its own model adapter so provider usage cannot race between
attempts.

Stateless MCP bindings are built for the exact execution attempt from a
versioned, credential-free manifest. The existing Contract/WorkUnit capability
resolver remains authoritative for scope and least privilege. `a2a.receive` is
an admission marker and cannot be declared as an MCP function.

Lifespan starts one worker task. Graceful shutdown requests a stop and waits for
the active claim; after a bounded deadline it cancels the task so existing
Runner cancellation supervision can fail the lease-fenced attempt. Health and
readiness expose sanitized process state only. Artifact bytes are written to an
explicit content-addressed local root that deployments must share with the
verifier.

## Consequences

The inbound vertical slice now has a strict process and container boundary
without granting Runner durable scheduling authority. Provider and MCP protocol
failures are honest WorkUnit failures rather than mock success. Mounted files
make credential rotation a deployment concern without copying secrets into
Mission state or the workspace catalog.

The service is not yet a general Runner pool. One configured process polls one
Mission, and readiness proves the Mission Control claim path only. Global
discovery, fleet fairness, distributed capacity, remote sandbox cancellation,
and external A2A conformance remain separate gates.

## Verification

Tests cover missing explicit configuration, forbidden model/URL/adapter values,
single-value secret loading, credential rejection in manifests, exact attempt
binding, tool schema forwarding, provider usage, response limits, sanitized
remote failures, malformed tool-call rejection, liveness/readiness content,
graceful drain, and forced cancellation after the shutdown deadline.
