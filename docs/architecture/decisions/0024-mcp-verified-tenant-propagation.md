# ADR-0024: MCP Verified Tenant Propagation

> Status: accepted
> Owner: protocol and security maintainers
> Date: 2026-08-09
> Scope: Tenant-scoped MCP Registry adapters

## Context

The MCP Registry's `call_agent` and `ingest_document` adapters wrote
`tenant_id: "default"` into downstream requests. That synthetic value severed
the connection between the authenticated caller and downstream data, could
mix tenants, and caused the Gateway's tenant isolation check to reject valid
production calls. Agent dispatch also omitted `actor_id` and did not forward a
credential to the authenticated `/publish` endpoint.

## Decision

Tenant-scoped Registry handlers use `shared/iam.TenantContext` as their only
tenant and actor source. Both non-empty tenant and actor are required; missing
identity fails before any downstream network call.

The MCP authentication middleware retains the already verified Authorization
header only in request-scoped Go context. `call_agent` forwards it unchanged
to the trusted AgentHub Gateway as an on-behalf-of credential, sends the
verified tenant and actor in the publish contract, and preserves the MCP trace
ID when available. The credential is never logged, returned, cached, audited,
or persisted.

`ingest_document` sends the verified tenant and records the verified actor in
document metadata. It does not invent a fallback tenant. Knowledge-service
authentication remains its own service boundary and is not added implicitly
by this decision.

## Consequences

Agent and document writes are attributable to the authenticated principal and
cannot silently land in a shared `default` tenant. Legacy SSE/STDIO calls to
these tenant-scoped tools now fail unless a trusted caller supplies an IAM
context; stateless `/mcp/rpc` supplies it through the required Bearer token.

The raw user credential exists in memory for the duration of one request so it
can reach the Gateway. Future workload identity or token exchange can replace
this on-behalf-of mechanism without changing Registry payload ownership.

## Alternatives considered

- Keep `default` for local mode: rejected because a convenience fallback must
  not become persisted business identity.
- Read tenant or actor from tool arguments: rejected because model-controlled
  arguments are not identity proof.
- Send tenant without a credential to Gateway: rejected because production
  Gateway authentication would fail and could not verify tenant ownership.
- Persist the Bearer token in Registry state: rejected because it would leak a
  credential across stateless requests.

## Verification

- Registry tests assert tenant, actor, trace, and Authorization on agent
  dispatch, and tenant/actor metadata on document ingest.
- Tests prove missing identity and missing downstream credentials fail before
  a network request.
- Authentication middleware tests prove the verified credential remains
  available in request context.
- MCP Go and Python contract regressions remain green.

## Supersedes

None. This completes tenant propagation left open by ADR-0022.
