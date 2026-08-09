# ADR-0025: MCP Session Listing Uses Gateway Tenant Scope

> Status: accepted
> Owner: protocol and security maintainers
> Date: 2026-08-09
> Scope: `list_sessions` MCP tool and session read path

## Context

The `list_sessions` Registry adapter called `/platform/sessions`, but the Go
Gateway did not expose that route. The adapter also omitted the authenticated
tenant and Bearer credential, accepted unbounded limits, and returned successful
tool content for downstream HTTP failures. The actual durable session query is
owned by `session-service`, whose internal `/sessions` endpoint requires a
tenant query parameter.

## Decision

Keep the Gateway as the authenticated public ingress for session listing:

1. The MCP Registry requires a verified IAM `TenantContext` and the original
   verified Authorization header before making the request.
2. The Registry injects the authenticated tenant as the only `tenant_id`
   value and normalizes `limit` to 1-50 (default 10). Tool arguments cannot
   override tenant identity.
3. Gateway serves `GET /platform/sessions`, rejects requests without a tenant
   context, overwrites any caller-provided tenant query, forwards the bounded
   query and credential, and proxies the response from Session Service.
4. Session Service applies the same 1-50 bound to its parameterized SQL query.
5. Non-2xx or invalid JSON responses are returned as MCP tool errors; no
   synthetic or partial success is manufactured.

## Consequences

Session data remains behind the Gateway's shared IAM middleware while durable
session ownership stays in Session Service. The duplicated limit validation is
intentional defense in depth at both protocol and persistence boundaries.
Docker deployments must configure `SESSION_SERVICE_URL` so the Gateway can
reach the internal service.

## Alternatives considered

- Connect MCP directly to Session Service: rejected because it would bypass
  the public IAM boundary and make every MCP deployment implement service auth.
- Trust a `tenant_id` tool argument or query: rejected because model/client
  input is not identity proof.
- Keep the missing `/platform/sessions` route: rejected because it makes a
  declared built-in tool non-functional and encourages fallback behavior.

## Verification

- Registry tests verify tenant and credential propagation, tenant override
  rejection, limit normalization, fail-closed identity, downstream status, and
  malformed JSON handling.
- Gateway proxy tests verify IAM tenant enforcement, query replacement,
  credential forwarding, and method rejection.
- Session Service tests verify the persistence-bound limit normalization.
